"""
metrics/milestones.py — Named early-game progression checkpoints.

A milestone becomes permanently reached when its condition is met.
Reached milestones never un-reach. check_milestones() mutates the
caller's `reached` set in place and also returns the newly reached ones.

Milestones that require entity placement or research state are defined
here but not yet evaluated — their conditions are stubbed with False
until the agent can query those game states.
"""

import json

from game_integration.factorio_bridge import execute_lua, is_error

MILESTONES: list[str] = [
    "first_resource_mined",     # any raw resource appears in inventory
    "first_craft",              # any crafted item appears in inventory
    "first_furnace_placed",     # stone-furnace placed in world (entity check)
    "first_iron_plate",         # iron-plate in inventory
    "first_automation",         # burner-mining-drill placed in world (entity check)
    "first_belt",               # transport-belt in inventory
    "first_assembler",          # assembling-machine-1 in inventory
    "research_started",         # any technology being researched (research check)
    "first_research_complete",  # any technology fully researched (research check)
    "iron_smelting_automated",  # iron-plate > 50 in inventory (+ furnace placed later)
]

_RAW_RESOURCES: frozenset[str] = frozenset({
    "iron-ore", "copper-ore", "coal", "stone",
    "crude-oil", "water", "wood", "uranium-ore",
})


def check_milestones(
    current_inventory: dict[str, int],
    reached: set[str],
) -> set[str]:
    """
    Check which milestones are newly met and add them to `reached`.

    Only inventory-checkable milestones are evaluated. Milestones that
    require entity placement or research state always return False here
    and must be wired up separately when those APIs are available.

    Args:
        current_inventory: current player inventory from get_player_inventory().
        reached: the persistent set of already-reached milestone names.
                 Mutated in place when new milestones are reached.

    Returns:
        Set of milestone names newly reached this call (may be empty).
    """
    newly: set[str] = set()

    def _reach(name: str) -> None:
        if name not in reached:
            reached.add(name)
            newly.add(name)

    inv = current_inventory

    # Inventory-checkable conditions
    _conditions: dict[str, bool] = {
        "first_resource_mined":    any(k in _RAW_RESOURCES for k in inv),
        "first_craft":             any(k not in _RAW_RESOURCES for k in inv),
        "first_furnace_placed":    False,   # TODO: entity placement check
        "first_iron_plate":        inv.get("iron-plate", 0) > 0,
        "first_automation":        False,   # TODO: entity placement check
        "first_belt":              inv.get("transport-belt", 0) > 0,
        "first_assembler":         inv.get("assembling-machine-1", 0) > 0,
        "research_started":        False,   # TODO: research state check
        "first_research_complete": False,   # TODO: research state check
        "iron_smelting_automated": inv.get("iron-plate", 0) > 50,  # TODO: add furnace check
    }

    for name in MILESTONES:
        if _conditions.get(name, False):
            _reach(name)

    return newly


def get_milestones_reached(reached: set[str]) -> list[str]:
    """
    Return reached milestones in canonical MILESTONES order.

    Args:
        reached: the persistent set of reached milestone names.

    Returns:
        Ordered list of reached milestone names.
    """
    return [m for m in MILESTONES if m in reached]


def get_next_milestone(reached: set[str]) -> str | None:
    """
    Return the next unreached milestone in MILESTONES order.

    Args:
        reached: the persistent set of reached milestone names.

    Returns:
        Name of the next milestone to reach, or None if all are reached.
    """
    for m in MILESTONES:
        if m not in reached:
            return m
    return None


def get_entities_placed(
    player_index: int = 1,
    client=None,
) -> dict[str, int]:
    """
    Return the count of each entity type placed by the player's force.

    Queries all entities on the player's current surface that belong to
    the player force, grouped by prototype name.

    Args:
        player_index: 1-based player index used to determine the surface
                      and force (default 1).
        client: RCON client. Uses module-level connection if omitted.

    Returns:
        {"entity-name": count, ...}, e.g. {"stone-furnace": 2, "burner-mining-drill": 1}
        Empty dict {} if nothing has been placed or on error.
    """
    lua = """
local pl = game.players[%d]
local ents = pl.surface.find_entities_filtered{force = pl.force}
local counts = {}
for _, e in ipairs(ents) do
  counts[e.name] = (counts[e.name] or 0) + 1
end
local parts = {}
for nm, cnt in pairs(counts) do
  parts[#parts+1] = '{"name":"' .. nm .. '","count":' .. cnt .. '}'
end
rcon.print('[' .. table.concat(parts, ',') .. ']')
""" % player_index
    result = execute_lua(lua.strip(), client)
    if is_error(result) or not result["output"]:
        return {}
    return {entry["name"]: entry["count"] for entry in json.loads(result["output"])}
