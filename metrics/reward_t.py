"""
metrics.py — Item value table and reward function for the Factorio agent.

V(i) = min over recipes r that produce i:
           (sum_j V(j) * c_{j,r}) * alpha(n_ingredients) / amount_of_i_in_r

alpha(n) = 1 + (n - 1) * 0.1

reward(t) = sum_i V(i) * (P_i(t) - C_i(t))

The value table is computed once at startup via build_value_table().
reward() is called every N steps (configurable).
"""

import json
from collections import defaultdict
from metrics.unique_items import update_unique_items
from game_integration.factorio_bridge import execute_lua, get_player_inventory

# ---------------------------------------------------------------------------
# Raw resource base values — cannot be crafted, only extracted.
# ---------------------------------------------------------------------------

BASE_VALUES: dict[str, float] = {
    "iron-ore": 1.0,
    "copper-ore": 1.0,
    "coal": 1.0,
    "stone": 1.0,
    "crude-oil": 2.0,
    "water": 0.1,
}

# ---------------------------------------------------------------------------
# Lua script: serialise all recipe prototypes to a JSON array.
#
# Output schema per element:
#   {"name": str,
#    "ingredients": [{"name": str, "amount": float}, ...],
#    "products":    [{"name": str, "amount": float}, ...]}
#
# "amount" for products is the *expected* amount:
#   amount * probability  (or (min+max)/2 * probability for ranged products).
# Recipes with no products are skipped.
# ---------------------------------------------------------------------------

_LUA_LOAD_RECIPES = """
local out = {}
for rname, recipe in pairs(prototypes.recipe) do
  local ings = {}
  for _, ing in ipairs(recipe.ingredients) do
    ings[#ings+1] = '{"name":"' .. ing.name .. '","amount":' .. (ing.amount or 1) .. '}'
  end
  local prods = {}
  for _, prod in ipairs(recipe.products) do
    local amt = prod.amount
    if amt == nil then
      amt = ((prod.amount_min or 0) + (prod.amount_max or 0)) / 2
    end
    amt = amt * (prod.probability or 1)
    if amt > 0 then
      prods[#prods+1] = '{"name":"' .. prod.name .. '","amount":' .. amt .. '}'
    end
  end
  if #prods > 0 then
    out[#out+1] = ('{"name":"' .. rname
      .. '","ingredients":[' .. table.concat(ings, ',')
      .. '],"products":['    .. table.concat(prods, ',')
      .. ']}')
  end
end
rcon.print('[' .. table.concat(out, ',') .. ']')
"""


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def alpha(n_ingredients: int) -> float:
    """Complexity multiplier: penalises recipes that combine many ingredients."""
    return 1.0 + (n_ingredients - 1) * 0.1


def load_recipes(client=None) -> list[dict]:
    """
    Fetch all recipe prototypes from the running Factorio server.

    Args:
        client: RCON client. Uses the module-level connection if omitted.

    Returns:
        List of recipe dicts:
            [{"name": str,
              "ingredients": [{"name": str, "amount": float}, ...],
              "products":    [{"name": str, "amount": float}, ...]}, ...]
    """
    raw = execute_lua(_LUA_LOAD_RECIPES.strip(), client)
    if not raw:
        return []
    return json.loads(raw)


def build_value_table(recipes: list[dict]) -> dict[str, float]:
    """
    Compute V(i) for every item/fluid reachable from BASE_VALUES.

    Algorithm: Bellman-Ford relaxation over the recipe graph.
    - Handles arbitrary cycles correctly (cyclic-only items converge to inf).
    - Takes at most len(item_recipes) passes to converge.

    Args:
        recipes: output of load_recipes().

    Returns:
        {"item-name": float, ...}
        Raw resources and all craftable items present.
        Items unreachable from BASE_VALUES are absent from the dict.
    """
    # Index: item name → all recipes that produce it
    item_recipes: dict[str, list[dict]] = defaultdict(list)
    for recipe in recipes:
        for product in recipe["products"]:
            item_recipes[product["name"]].append(recipe)

    # Seed with raw resource values; everything else starts at infinity.
    values: dict[str, float] = defaultdict(lambda: float("inf"))
    values.update(BASE_VALUES)

    # Relax until no value improves. In the worst case (a linear chain of
    # length N) we need N passes, so cap at len(item_recipes) + 1.
    for _ in range(len(item_recipes) + 1):
        changed = False
        for item, recipe_list in item_recipes.items():
            best = values[item]

            for recipe in recipe_list:
                # Per-unit cost: divide total recipe cost by how much of
                # `item` this recipe actually produces.
                item_amount = next(
                    (p["amount"] for p in recipe["products"] if p["name"] == item),
                    1.0,
                )
                if item_amount <= 0:
                    continue

                ingredient_cost = 0.0
                for ing in recipe["ingredients"]:
                    v = values[ing["name"]]
                    if v == float("inf"):
                        ingredient_cost = float("inf")
                        break
                    ingredient_cost += v * ing["amount"]

                if ingredient_cost == float("inf"):
                    continue

                n = len(recipe["ingredients"])
                candidate = (ingredient_cost * alpha(n)) / item_amount
                if candidate < best:
                    best = candidate
                    changed = True

            values[item] = best

        if not changed:
            break

    # Drop items that are still unreachable.
    return {k: v for k, v in values.items() if v < float("inf")}


# ---------------------------------------------------------------------------
# Reward configuration
# ---------------------------------------------------------------------------

# How often the agent loop should call compute_reward() (every N steps).
REWARD_INTERVAL: int = 60

# Inventory snapshot from the previous compute_reward() call.
# Delta between snapshots = net items produced minus items consumed.
_previous_inventory: dict[str, int] = {}


def compute_reward(
    values: dict[str, float],
    player_index: int = 1,
    client=None,
) -> tuple[float, dict[str, float]]:
    """
    Compute reward(t) = sum_i V(i) * (inventory_i[t] - inventory_i[t-1]).

    Approximates P_i(t) - C_i(t) as the net change in player inventory
    between successive calls. Items that increased contribute positively
    (net production); items that decreased contribute negatively (net
    consumption / crafting cost).

    Args:
        values: precomputed value table from build_value_table().
        player_index: 1-based player index (default 1).
        client: RCON client. Uses module-level connection if omitted.

    Returns:
        (total_reward, breakdown)
          total_reward — float, weighted sum of inventory deltas
          breakdown    — {item_name: contribution}, non-zero entries only,
                         sorted by descending absolute contribution.
    """
    global _previous_inventory

    current = get_player_inventory(player_index, client)

    update_unique_items(current, _previous_inventory)

    all_items = set(current) | set(_previous_inventory)
    breakdown: dict[str, float] = {}
    for name in all_items:
        v = values.get(name)
        if v is None:
            continue
        delta = current.get(name, 0) - _previous_inventory.get(name, 0)
        contribution = v * delta
        if contribution != 0.0:
            breakdown[name] = contribution

    _previous_inventory = dict(current)

    total = sum(breakdown.values())
    breakdown = dict(
        sorted(breakdown.items(), key=lambda kv: abs(kv[1]), reverse=True)
    )
    return total, breakdown