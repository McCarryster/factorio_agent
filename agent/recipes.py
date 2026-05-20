"""
Runtime recipe queries from Factorio via RCON.
Caches results so we don't query every iteration.

Usage:
    from recipes import get_recipe, get_craftable_count, MILESTONE_RECIPES

    recipe = get_recipe(client, "burner-mining-drill")
    # {"name": "burner-mining-drill", "ingredients": {"iron-plate": 3, ...},
    #  "products": {"burner-mining-drill": 1}, "enabled": True}

    n = get_craftable_count(client, "transport-belt", inventory)
    # How many transport-belts can we craft given current inventory
"""

from __future__ import annotations
from typing import Optional

# ---------------------------------------------------------------------------
# Cache — populated on first query per game session
# ---------------------------------------------------------------------------
_recipe_cache: dict[str, dict] = {}
_enabled_cache: dict[str, bool] = {}  # per-recipe enabled status


def get_recipe(client, name: str) -> Optional[dict]:
    """
    Return recipe dict for item name, queried from game.
    Returns None if recipe doesn't exist.
    
    Dict format:
        {
            "name": str,
            "ingredients": {item_name: count, ...},
            "products": {item_name: count, ...},
            "enabled": bool,  # whether player force has it unlocked
        }
    """
    if name in _recipe_cache:
        # Re-check enabled status (may have changed due to research)
        recipe = _recipe_cache[name].copy()
        recipe["enabled"] = _get_enabled(client, name)
        return recipe

    from game_integration.factorio_bridge import execute_lua

    lua = f"""
local proto = prototypes.recipe["{name}"]
if not proto then rcon.print("NONE") return end
local force_rec = game.forces.player.recipes["{name}"]
local enabled = force_rec and force_rec.enabled or false

local parts = {{}}
table.insert(parts, "enabled=" .. tostring(enabled))

for _, ing in ipairs(proto.ingredients) do
    table.insert(parts, "ing:" .. ing.name .. "=" .. ing.amount)
end
for _, prod in ipairs(proto.products) do
    local amt = prod.amount or prod.amount_min or 1
    table.insert(parts, "prod:" .. prod.name .. "=" .. amt)
end
rcon.print(table.concat(parts, "|"))
"""
    result = execute_lua(client, lua)
    raw = result.get("output", "").strip()
    if not raw or raw == "NONE":
        return None

    recipe = {"name": name, "ingredients": {}, "products": {}, "enabled": False}
    for part in raw.split("|"):
        if part.startswith("enabled="):
            recipe["enabled"] = part.split("=", 1)[1] == "true"
        elif part.startswith("ing:"):
            rest = part[4:]
            item, count = rest.rsplit("=", 1)
            recipe["ingredients"][item] = int(count)
        elif part.startswith("prod:"):
            rest = part[5:]
            item, count = rest.rsplit("=", 1)
            recipe["products"][item] = int(count)

    _recipe_cache[name] = recipe
    return recipe


def _get_enabled(client, name: str) -> bool:
    """Check current enabled status of a recipe (respects research state)."""
    from game_integration.factorio_bridge import execute_lua
    lua = f"""
local r = game.forces.player.recipes["{name}"]
rcon.print(r and tostring(r.enabled) or "false")
"""
    result = execute_lua(client, lua)
    return result.get("output", "").strip() == "true"


def get_craftable_count(client, name: str, inventory: dict[str, int]) -> int:
    """
    How many of `name` can be crafted given current inventory?
    Returns 0 if recipe is disabled or ingredients missing.
    """
    recipe = get_recipe(client, name)
    if not recipe or not recipe["enabled"]:
        return 0

    # Products per craft
    product_count = recipe["products"].get(name, 1)

    # How many batches can we do?
    batches = float("inf")
    for ing_name, ing_count in recipe["ingredients"].items():
        have = inventory.get(ing_name, 0)
        batches = min(batches, have // ing_count)

    if batches == float("inf"):
        batches = 0
    return int(batches) * product_count


def get_all_recipes(client, names: list[str]) -> dict[str, dict]:
    """Batch fetch multiple recipes."""
    return {n: r for n in names if (r := get_recipe(client, n)) is not None}


def format_recipe(recipe: dict) -> str:
    """Human-readable recipe string for LLM prompts."""
    if not recipe:
        return "unknown"
    ings = ", ".join(f"{v}x {k}" for k, v in recipe["ingredients"].items())
    prods = ", ".join(f"{v}x {k}" for k, v in recipe["products"].items())
    status = "✓" if recipe["enabled"] else "✗ (locked)"
    return f"{recipe['name']} {status}: [{ings}] → [{prods}]"


# ---------------------------------------------------------------------------
# Items the agent might want to craft — in dependency order
# ---------------------------------------------------------------------------
AGENT_RECIPES = [
    # Tier 0 — always enabled
    "stone-furnace",
    "iron-gear-wheel",
    "transport-belt",
    "iron-chest",
    "wooden-chest",
    "burner-mining-drill",
    # Tier 1 — unlocked by 10 copper plates (electronics)
    "copper-cable",
    "electronic-circuit",
    "small-electric-pole",
    "inserter",
    # Tier 2 — unlocked by 50 iron plates (steam-power)
    "pipe",
    "boiler",
    "steam-engine",
    "offshore-pump",
]

# ---------------------------------------------------------------------------
# Resource requirements for factory blocks
# ---------------------------------------------------------------------------

def requirements_coal_mining(num_drills: int = 2) -> dict[str, int]:
    """Items needed to place a coal mining block."""
    # collector belt: num_drills tiles + 2 turn + 2 output
    # chain inserters: num_drills - 1 + 1 self-feed
    belts = num_drills + 4
    inserters = num_drills
    return {
        "burner-mining-drill": num_drills,
        "transport-belt": belts,
        "inserter": inserters,
        "coal": 10,  # seed fuel
    }


def requirements_iron_mining(num_drills: int = 5) -> dict[str, int]:
    """Items needed to place an iron mining block."""
    # main belt: num_drills*3 + 3 tiles + 1 turn
    belts = num_drills * 3 + 4
    inserters = num_drills  # chain + feed
    return {
        "burner-mining-drill": num_drills,
        "transport-belt": belts,
        "inserter": inserters,
        "coal": 5,  # seed fuel
    }


def requirements_smelting(num_furnaces: int = 5) -> dict[str, int]:
    """Items needed to place a smelting block."""
    # output belt: num_furnaces*3 + 3 tiles
    belts = num_furnaces * 3 + 4
    inserters = num_furnaces * 2  # input + output per furnace
    return {
        "stone-furnace": num_furnaces,
        "transport-belt": belts,
        "inserter": inserters,
        "iron-chest": 1,
        "coal": 5,  # seed fuel
    }


def requirements_steam_network() -> dict[str, int]:
    """Items needed to place a steam power network."""
    return {
        "offshore-pump": 1,
        "boiler": 1,
        "steam-engine": 1,
        "pipe": 5,
        "small-electric-pole": 3,
        "coal": 50,  # boiler fuel
    }


def requirements_power_line(num_poles: int = 20) -> dict[str, int]:
    """Items needed to run a power line to factory."""
    return {
        "small-electric-pole": num_poles,
    }


def total_requirements(
    num_coal_drills: int = 2,
    num_iron_drills: int = 5,
    num_furnaces: int = 5,
    num_poles: int = 20,
) -> dict[str, int]:
    """
    Total items needed to build the complete factory.
    Used by the agent to know when it has crafted enough.
    """
    totals: dict[str, int] = {}

    def add(d: dict[str, int]):
        for k, v in d.items():
            totals[k] = totals.get(k, 0) + v

    add(requirements_coal_mining(num_coal_drills))
    add(requirements_iron_mining(num_iron_drills))
    add(requirements_smelting(num_furnaces))
    add(requirements_steam_network())
    add(requirements_power_line(num_poles))

    return totals


if __name__ == "__main__":
    print("Total factory requirements:")
    reqs = total_requirements()
    for item, count in sorted(reqs.items()):
        print(f"  {item}: {count}")