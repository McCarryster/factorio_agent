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


def get_nearby_cluster_resources(
    radius: int = 32,
    player_index: int = 1,
    client: factorio_rcon.RCONClient | None = None,
) -> list[dict[str, Any]]:
    """
    Return one cluster entry per resource type within a radius of the player.

    Aggregates all individual ore entities of the same type into a single
    entry with centroid position and total amount, so the agent sees one
    iron-ore cluster instead of thousands of individual tiles.

    Args:
        radius: Search radius in tiles (default 32).
        player_index: 1-based player index (default 1).
        client: RCON client. Uses module-level connection if omitted.

    Returns:
        List of cluster dicts, one per resource type found:
          - "name"   (str)   resource prototype name, e.g. "iron-ore"
          - "x"      (float) centroid X of all matching ore tiles
          - "y"      (float) centroid Y of all matching ore tiles
          - "amount" (int)   total remaining resource units
          - "count"  (int)   number of individual ore tiles in cluster
        Empty list [] if none found.

    Example:
        clusters = get_nearby_cluster_resources(radius=128)
        for c in clusters:
            print(c["name"], c["x"], c["y"], c["amount"])
    """
    lua = """
local pl = game.players[%d]
local ents = pl.surface.find_entities_filtered{position=pl.position, radius=%d, type="resource"}
local clusters = {}
for _, e in ipairs(ents) do
  local nm = e.name
  if not clusters[nm] then clusters[nm] = {x=0, y=0, amount=0, count=0} end
  local c = clusters[nm]
  c.x = c.x + e.position.x
  c.y = c.y + e.position.y
  c.amount = c.amount + (e.amount or 0)
  c.count = c.count + 1
end
local parts = {}
for nm, c in pairs(clusters) do
  local cx = c.x / c.count
  local cy = c.y / c.count
  parts[#parts+1] = ('{"name":"' .. nm .. '","x":' .. cx .. ',"y":' .. cy
    .. ',"amount":' .. c.amount .. ',"count":' .. c.count .. '}')
end
rcon.print('[' .. table.concat(parts, ',') .. ']')
""" % (player_index, radius)
    result = execute_lua(lua.strip(), client)
    return json.loads(result["output"]) if not is_error(result) and result["output"] else []