"""
Factorio reward calculator (Factorio 2.0+ API).

Implements:  V(i) = min_{r in R_i} ( (sum_{j in I_r} V(j) * c_{j,r}) * alpha(|I_r|) + E(r, C_r) )

reward at step t = V_total(t) - V_total(t-1)

NOTE: All Lua snippets below use rcon.print(...) to emit output,
to match the project's execute_lua wrapper which does not capture
return values from pcall.
"""

import json
import math
from typing import Optional

import factorio_rcon

# Use the project's existing wrapper:
from game_integration.factorio_bridge import execute_lua

# ============================================================
# Tunable hyperparameters
# ============================================================

ALPHA_K = 0.1
# Energy cost = ENERGY_SCALE * recipe_time_seconds. Tiny so it acts as a tie-breaker
# rather than dominating ingredient cost. Tune up if you want the agent to care
# about fast crafting, down if it shouldn't matter much.
ENERGY_SCALE = 0.05
DEFAULT_CRAFTER_POWER = 75_000  # kept for reference but no longer used directly


def alpha(n: int) -> float:
    return 1.0 + ALPHA_K * max(0, n - 1)


# ============================================================
# Raw resources (base case for V)
# ============================================================

RAW_VALUES: dict[str, float] = {
    "iron-ore": 1.0, "copper-ore": 1.0, "stone": 1.0, "coal": 1.0,
    "wood": 0.5, "raw-fish": 1.0, "uranium-ore": 8.0,
    "crude-oil": 0.5, "water": 0.01, "steam": 0.05,
    # Space Age raws
    "calcite": 1.5, "tungsten-ore": 4.0, "scrap": 0.5,
    "holmium-ore": 5.0, "lithium-brine": 1.0, "fluorine": 1.0,
    "ammoniacal-solution": 0.5,
}


# ============================================================
# Lua dumps — all use rcon.print() to emit output
# ============================================================

_LIST_RECIPE_NAMES_LUA = """
local names = {}
for name, _ in pairs(prototypes.recipe) do
    names[#names+1] = name
end
rcon.print(helpers.table_to_json(names))
"""

_DUMP_RECIPES_BATCH_LUA = """
local names = helpers.json_to_table([==[__NAMES_JSON__]==])
local out = {}
for _, name in pairs(names) do
    local proto = prototypes.recipe[name]
    if proto then
        local ingredients = {}
        for _, ing in pairs(proto.ingredients or {}) do
            ingredients[#ingredients+1] = { name = ing.name, amount = ing.amount, type = ing.type }
        end
        local products = {}
        for _, prod in pairs(proto.products or {}) do
            local amt = prod.amount
            if amt == nil then
                amt = ((prod.amount_min or 0) + (prod.amount_max or 0)) / 2
            end
            local prob = prod.probability or 1
            products[#products+1] = { name = prod.name, amount = amt * prob, type = prod.type }
        end
        out[name] = {
            ingredients = ingredients,
            products = products,
            energy = proto.energy,
            category = proto.category,
        }
    end
end
rcon.print(helpers.table_to_json(out))
"""

_DUMP_ENTITY_TO_ITEM_LUA = """
local entity_to_item = {}
for name, proto in pairs(prototypes.item) do
    if proto.place_result then
        entity_to_item[proto.place_result.name] = name
    end
end
rcon.print(helpers.table_to_json(entity_to_item))
"""


def _safe_json_loads(s: str, ctx: str) -> dict | list:
    if not s or not s.strip():
        raise RuntimeError(f"{ctx}: Factorio returned empty string. "
                           f"Possible RCON truncation — try smaller batch size.")
    try:
        return json.loads(s)
    except json.JSONDecodeError as e:
        preview = s[:200].replace("\n", " ")
        raise RuntimeError(f"{ctx}: JSON decode failed ({e}). "
                           f"Length={len(s)}, preview={preview!r}")


class RewardCalculator:
    # Lower this if you see truncation. RCON typical limit is ~4 KB.
    RECIPE_BATCH_SIZE = 30

    def __init__(self, client: factorio_rcon.RCONClient):
        self.client = client
        self.recipes: dict = {}
        self.entity_to_item: dict = {}
        self.item_to_recipes: dict[str, list[str]] = {}
        self.v_cache: dict[str, float] = {}
        self.prev_total_v: Optional[float] = None
        self.unique_items_produced: set[str] = set()

        self._load_recipe_data()
        self._build_item_recipe_index()
        self._precompute_all_values()

    # ---------- chunked recipe loading ----------

    def _load_recipe_data(self) -> None:
        # 1. recipe names (small list)
        result = execute_lua(self.client, _LIST_RECIPE_NAMES_LUA)
        if result["status"] != "OK":
            raise RuntimeError(f"Recipe-name listing failed: {result['output']}")
        names = _safe_json_loads(result["output"], "recipe names")
        if not isinstance(names, list):
            raise RuntimeError(f"Expected list of names, got {type(names)}")
        print(f"[reward] {len(names)} recipes to fetch")

        # 2. fetch recipe details in batches
        merged: dict = {}
        for i in range(0, len(names), self.RECIPE_BATCH_SIZE):
            batch = names[i:i + self.RECIPE_BATCH_SIZE]
            # JSON-encode and inject via replace (avoids str.format issues with Lua's {})
            names_json = json.dumps(batch)
            lua = _DUMP_RECIPES_BATCH_LUA.replace("__NAMES_JSON__", names_json)
            result = execute_lua(self.client, lua)
            if result["status"] != "OK":
                raise RuntimeError(f"Batch {i} failed: {result['output']}")
            batch_data = _safe_json_loads(
                result["output"], f"recipe batch {i}-{i+len(batch)}"
            )
            merged.update(batch_data)
        self.recipes = merged

        # 3. entity_to_item map
        result = execute_lua(self.client, _DUMP_ENTITY_TO_ITEM_LUA)
        if result["status"] != "OK":
            raise RuntimeError(f"entity_to_item dump failed: {result['output']}")
        self.entity_to_item = _safe_json_loads(result["output"], "entity_to_item") # type: ignore

    def _build_item_recipe_index(self) -> None:
        # Categories whose recipes inflate item value because they produce
        # things "from thin air" (recycling) or are special engine-internal recipes.
        EXCLUDED_CATEGORIES = {
            "recycling",
            "captive-spawner-process",
            "asteroid-collector",
            "rocket-building",
            "parameters",
        }

        for r_name, r in self.recipes.items():
            cat = r.get("category") or ""
            if cat in EXCLUDED_CATEGORIES:
                continue
            # Skip if recipe ends with "-recycling" even if mod authors used a different category
            if r_name.endswith("-recycling"):
                continue

            products = r.get("products") or []
            if not products:
                continue

            # Identify primary product: the one with the largest output amount.
            # Byproducts (small probability/amount items) shouldn't count this recipe
            # toward their value.
            primary = max(products, key=lambda p: p.get("amount", 0) or 0)

            for prod in products:
                # Only index this recipe under items where the item is primary OR
                # produces in equal amount (handles single-product recipes naturally).
                if prod["name"] == primary["name"]:
                    self.item_to_recipes.setdefault(prod["name"], []).append(r_name)

    # ---------- V(i) ----------

    def _energy_cost(self, recipe: dict) -> float:
        # Energy proxied by recipe crafting time. Small coefficient so that
        # ingredient cost dominates V; energy mainly distinguishes equally-priced recipes.
        return (recipe["energy"] or 0.5) * ENERGY_SCALE

    def V(self, item: str, _stack: Optional[set] = None) -> float:
        if item in self.v_cache:
            return self.v_cache[item]
        if item in RAW_VALUES:
            self.v_cache[item] = RAW_VALUES[item]
            return RAW_VALUES[item]

        if _stack is None:
            _stack = set()
        if item in _stack:
            # Cycle: return inf so this branch is rejected by the min, but DO NOT cache.
            return math.inf
        _stack = _stack | {item}

        recipe_names = self.item_to_recipes.get(item, [])
        if not recipe_names:
            # Truly no recipe: treat as raw with default value 1.0
            self.v_cache[item] = 1.0
            return 1.0

        best = math.inf
        for r_name in recipe_names:
            r = self.recipes[r_name]
            ingredients = r["ingredients"]

            output_amount = 0.0
            for prod in r["products"]:
                if prod["name"] == item:
                    output_amount += prod["amount"]
            if output_amount <= 0:
                continue

            try:
                ingredient_cost = sum(
                    self.V(ing["name"], _stack) * ing["amount"] for ing in ingredients
                )
            except RecursionError:
                continue
            if not math.isfinite(ingredient_cost):
                continue  # one of the ingredients hit a cycle on this branch

            total = (ingredient_cost * alpha(len(ingredients)) + self._energy_cost(r)) / output_amount
            if total < best:
                best = total

        if math.isfinite(best):
            self.v_cache[item] = best
            return best
        else:
            # Every recipe was unreachable on this call due to cycles. Don't cache —
            # the precompute pass below will resolve the order eventually.
            return math.inf

    def _precompute_all_values(self) -> None:
        # Iterate to a fixed point. Items whose ingredients haven't been computed
        # yet may return inf the first time; later passes find them.
        for _ in range(5):
            unresolved = 0
            for item in list(self.item_to_recipes.keys()):
                if item in self.v_cache:
                    continue
                v = self.V(item)
                if not math.isfinite(v):
                    unresolved += 1
            if unresolved == 0:
                break
        # Anything still unresolved gets a sentinel default
        for item in self.item_to_recipes:
            if item not in self.v_cache:
                self.v_cache[item] = 1.0

    # ---------- snapshot ----------

    _SNAPSHOT_LUA = """
    local p = game.players[1]
    if not p or not p.character then
        rcon.print(helpers.table_to_json({inv={}, world={}}))
        return
    end
    local force = p.force
    local surface = p.surface

    local inv = {}
    local function add(item, count)
        if not item or not count or count == 0 then return end
        inv[item] = (inv[item] or 0) + count
    end

    local function read_inv(luainv)
        if not luainv or not luainv.valid then return end
        local contents = luainv.get_contents()
        if not contents then return end
        if contents[1] ~= nil and type(contents[1]) == "table" then
            for _, entry in pairs(contents) do
                add(entry.name, entry.count)
            end
        else
            for name, count in pairs(contents) do
                add(name, count)
            end
        end
    end

    for _, inv_type in pairs({
        defines.inventory.character_main,
        defines.inventory.character_guns,
        defines.inventory.character_ammo,
        defines.inventory.character_armor,
        defines.inventory.character_trash,
    }) do
        read_inv(p.get_inventory(inv_type))
    end

    if p.crafting_queue then
        for _, q in pairs(p.crafting_queue) do
            if q.recipe then
                local rp = prototypes.recipe[q.recipe]
                if rp then
                    for _, prod in pairs(rp.products or {}) do
                        local amt = prod.amount or ((prod.amount_min or 0) + (prod.amount_max or 0)) / 2
                        add(prod.name, (amt or 0) * (q.count or 1))
                    end
                end
            end
        end
    end

    local world = {}
    for _, e in pairs(surface.find_entities_filtered{force = force}) do
        if e.valid and e.type ~= "character" then
            world[e.name] = (world[e.name] or 0) + 1
            pcall(function() read_inv(e.get_output_inventory()) end)
            pcall(function() read_inv(e.get_module_inventory()) end)
            pcall(function() read_inv(e.get_fuel_inventory()) end)
            if e.type == "container" or e.type == "logistic-container" or e.type == "infinity-container" then
                pcall(function() read_inv(e.get_inventory(defines.inventory.chest)) end)
            end
            if e.type == "furnace" then
                pcall(function() read_inv(e.get_inventory(defines.inventory.furnace_source)) end)
            end
            if e.type == "assembling-machine" then
                pcall(function() read_inv(e.get_inventory(defines.inventory.assembling_machine_input)) end)
            end
        end
    end

    rcon.print(helpers.table_to_json({inv = inv, world = world}))
    """

    def _snapshot(self) -> tuple[dict[str, int], dict[str, int]]:
        result = execute_lua(self.client, self._SNAPSHOT_LUA)
        if result["status"] != "OK":
            raise RuntimeError(f"Snapshot failed: {result['output']}")
        data = _safe_json_loads(result["output"], "snapshot")
        return data.get("inv") or {}, data.get("world") or {} # type: ignore

    # ---------- aggregation ----------

    def _total_v(self, inv: dict[str, int], world: dict[str, int]) -> float:
        total = 0.0
        for item, count in inv.items():
            v = self.V(item)
            if math.isfinite(v):
                total += v * count
        for entity, count in world.items():
            item = self.entity_to_item.get(entity, entity)
            v = self.V(item)
            if math.isfinite(v):
                total += v * count
        return total

    # ---------- public API ----------

    def reset(self) -> None:
        inv, world = self._snapshot()
        self.prev_total_v = self._total_v(inv, world)

    def get_reward(self) -> float:
        inv, world = self._snapshot()

        # update unique items from current inventory
        for item in inv:
            self.unique_items_produced.add(item)

        current_v = self._total_v(inv, world)
        if self.prev_total_v is None:
            self.prev_total_v = current_v
            return 0.0
        reward = current_v - self.prev_total_v
        self.prev_total_v = current_v
        return reward


# ============================================================
# Demo
# ============================================================

if __name__ == "__main__":
    import time
    from game_integration.factorio_bridge import get_client

    client = get_client()
    calc = RewardCalculator(client)
    print(f"Loaded {len(calc.recipes)} recipes, {len(calc.entity_to_item)} entity mappings")
    calc.reset()

    reward = calc.get_reward()
    print(f"Baseline taken {reward}")

    execute_lua(client, "game.players[1].insert{name='stone-wall', count=2}")
    time.sleep(1)
    reward = calc.get_reward()
    print(f"Delta reward {reward}")