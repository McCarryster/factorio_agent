"""
TODO: description
"""


import json
import factorio_rcon
from game_integration.factorio_bridge import execute_lua, is_error
from typing import Any



def get_player_position(
    player_index: int = 1,
    client: factorio_rcon.RCONClient | None = None,
) -> dict[str, float]:
    """
    Return the current map position of a player.

    Args:
        player_index: 1-based player index (default 1).
        client: RCON client. Uses module-level connection if omitted.

    Returns:
        {"x": float, "y": float}

    Example:
        pos = get_player_position()
        print(pos["x"], pos["y"])
    """
    lua = (
        """local p = game.players[%d].position; """
        """rcon.print('{"x":' .. p.x .. ',"y":' .. p.y .. '}')"""
    ) % player_index
    result = execute_lua(lua, client)
    return json.loads(result["output"]) if not is_error(result) and result["output"] else {}


def get_player_inventory(
    player_index: int = 1,
    client: factorio_rcon.RCONClient | None = None,
) -> dict[str, int]:
    """
    Return the contents of a player's main inventory.

    Args:
        player_index: 1-based player index (default 1).
        client: RCON client. Uses module-level connection if omitted.

    Returns:
        {"item-name": count, ...}  e.g. {"iron-plate": 10, "coal": 5}
        Empty dict {} if inventory is empty.

    Example:
        inv = get_player_inventory()
        print(inv.get("iron-plate", 0))
    """
    lua = """
local inv      = game.players[%d].get_main_inventory()
local contents = inv.get_contents()
local out      = {}
for _, item in ipairs(contents) do
  out[#out+1] = '{"name":"' .. item.name .. '","count":' .. item.count .. '}'
end
rcon.print('[' .. table.concat(out, ',') .. ']')
""" % player_index
    result = execute_lua(lua.strip(), client)
    if is_error(result) or not result["output"]:
        return {}
    counts: dict[str, int] = {}
    for item in json.loads(result["output"]):
        counts[item["name"]] = counts.get(item["name"], 0) + item["count"]
    return counts


def get_nearby_resources(
    radius: int = 32,
    player_index: int = 1,
    client: factorio_rcon.RCONClient | None = None,
) -> list[dict[str, Any]]:
    """
    Return resource entities (ore patches) within a given radius of the player.

    Args:
        radius: Search radius in tiles (default 32).
        player_index: 1-based player index (default 1).
        client: RCON client. Uses module-level connection if omitted.

    Returns:
        List of resource dicts, each with:
          - "name"   (str)   entity prototype name, e.g. "iron-ore"
          - "x"      (float) map X coordinate
          - "y"      (float) map Y coordinate
          - "amount" (int)   remaining resource units
        Empty list [] if none found.

    Example:
        resources = get_nearby_resources(radius=64)
        iron = [r for r in resources if r["name"] == "iron-ore"]
    """
    lua = (
        """local pl = game.players[%d]; """
        """local ents = pl.surface.find_entities_filtered{position=pl.position, radius=%d, type="resource"}; """
        """local parts = {}; """
        """for _, e in ipairs(ents) do """
        """  table.insert(parts, '{"name":"' .. e.name .. '","x":' .. e.position.x .. ',"y":' .. e.position.y .. ',"amount":' .. (e.amount or 0) .. '}') """
        """end; """
        """rcon.print('[' .. table.concat(parts, ',') .. ']')"""
    ) % (player_index, radius)
    result = execute_lua(lua, client)
    return json.loads(result["output"]) if not is_error(result) and result["output"] else []