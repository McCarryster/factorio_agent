"""
TODO: description
"""


import json
import factorio_rcon
from game_integration.factorio_bridge import execute_lua
from typing import Any



def get_player_position(client: factorio_rcon.RCONClient, player_index: int = 1) -> dict[str, float]:
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
    result = execute_lua(client, lua)
    return json.loads(result["output"]) if result['status'] == "OK" and result["output"] else {}


def get_player_inventory(client: factorio_rcon.RCONClient, player_index: int = 1) -> dict[str, int]:
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
    result = execute_lua(client, lua.strip())
    if result['status'] == "ERROR" or not result["output"]:
        return {}
    counts: dict[str, int] = {}
    for item in json.loads(result["output"]):
        counts[item["name"]] = counts.get(item["name"], 0) + item["count"]
    return counts


def get_nearby_resources(
    client: factorio_rcon.RCONClient,
    radius: int = 32,
    player_index: int = 1,
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
    result = execute_lua(client, lua)
    return json.loads(result["output"]) if result['status'] == "OK" and result["output"] else []


def get_nearby_cluster_resources(
    client: factorio_rcon.RCONClient,
    radius: int = 32,
    player_index: int = 1,
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
    result = execute_lua(client, lua.strip())
    return json.loads(result["output"]) if result['status'] == "OK" and result["output"] else []


# Symbol mapping for common entities
ENTITY_SYMBOLS = {
    "burner-mining-drill": "D",
    "electric-mining-drill": "D",
    "stone-furnace": "F",
    "steel-furnace": "F",
    "electric-furnace": "F",
    "inserter": "I",
    "burner-inserter": "I",
    "fast-inserter": "I",
    "long-handed-inserter": "I",
    "transport-belt": "B",
    "fast-transport-belt": "B",
    "express-transport-belt": "B",
    "underground-belt": "U",
    "splitter": "S",
    "assembling-machine-1": "A",
    "assembling-machine-2": "A",
    "assembling-machine-3": "A",
    "boiler": "O",
    "steam-engine": "E",
    "offshore-pump": "P",
    "pipe": "p",
    "small-electric-pole": "T",
    "medium-electric-pole": "T",
    "iron-chest": "C",
    "wooden-chest": "C",
    "steel-chest": "C",
}

# Resource symbols (lowercase to distinguish from buildings)
RESOURCE_SYMBOLS = {
    "iron-ore": "i",
    "copper-ore": "c",
    "coal": "k",
    "stone": "s",
    "uranium-ore": "u",
    "crude-oil": "o",
}

_LUA_LOCAL_MAP = """
local player = game.players[1]
local pos = player.position
local surface = game.surfaces['nauvis']
local r = __RADIUS__
local area = {{pos.x - r, pos.y - r}, {pos.x + r, pos.y + r}}
local out = {}
local entities = surface.find_entities_filtered{area=area, force="player"}
for _, e in ipairs(entities) do
    if e.type ~= "character" then
        out[#out+1] = '{"name":"' .. e.name .. '","x":' .. e.position.x .. ',"y":' .. e.position.y .. ',"dir":' .. (e.direction or 0) .. '}'
    end
end
local resources = surface.find_entities_filtered{area=area, type="resource"}
for _, r_ent in ipairs(resources) do
    out[#out+1] = '{"name":"' .. r_ent.name .. '","x":' .. r_ent.position.x .. ',"y":' .. r_ent.position.y .. ',"dir":0}'
end
rcon.print('[' .. table.concat(out, ',') .. ']')
"""


def get_local_map(client, radius: int = 10) -> str:
    """
    Build an ASCII map of entities and resources around the player.
    Returns a formatted string with coordinate labels and legend.
    """
    # Query player position first
    pos_result = execute_lua(client, "local p = game.players[1].position; rcon.print(p.x .. ',' .. p.y)")
    if pos_result["status"] != "OK":
        return "LOCAL MAP: (error fetching player position)"
    px, py = map(float, pos_result["output"].split(","))
    pcx, pcy = int(round(px)), int(round(py))

    # Query entities and resources
    lua = _LUA_LOCAL_MAP.replace("__RADIUS__", str(radius))
    result = execute_lua(client, lua)
    if result["status"] != "OK":
        return f"LOCAL MAP: (error fetching entities: {result['output']})"

    try:
        entities = json.loads(result["output"]) if result["output"] else []
    except json.JSONDecodeError:
        return "LOCAL MAP: (parse error)"

    # Build grid
    size = radius * 2 + 1
    grid = [["." for _ in range(size)] for _ in range(size)]

    # Place entities
    used_symbols: dict[str, str] = {}
    entities_sorted = sorted(entities, key=lambda e: 0 if e["name"] in RESOURCE_SYMBOLS else 1)

    for e in entities_sorted:
        gx = int(round(e["x"])) - pcx + radius
        gy = int(round(e["y"])) - pcy + radius
        if 0 <= gx < size and 0 <= gy < size:
            symbol = ENTITY_SYMBOLS.get(e["name"]) or RESOURCE_SYMBOLS.get(e["name"]) or "?"
            grid[gy][gx] = symbol
            used_symbols[symbol] = e["name"]

    # Place player at center
    if grid[radius][radius] == ".":
        grid[radius][radius] = "@"

    # Format with coordinate labels
    lines = []
    lines.append(f"LOCAL MAP ({size}x{size} tiles around player at ({pcx}, {pcy})):")

    # Column header
    col_header = "      " + " ".join(f"{(pcx - radius + i) % 100:>2}" for i in range(size))
    lines.append(col_header)

    # Rows with row labels
    for i, row in enumerate(grid):
        row_y = pcy - radius + i
        lines.append(f"  {row_y:>4}  " + "  ".join(row))

    # Legend (only for symbols actually used)
    if used_symbols:
        legend_parts = [f"{s}={name}" for s, name in sorted(used_symbols.items())]
        lines.append("Legend: @=player, " + ", ".join(legend_parts))
    else:
        lines.append("Legend: @=player (no other entities in range)")

    return "\n".join(lines)



if __name__ == "__main__":
    from game_integration.dependencies import get_factorio_client
    client = get_factorio_client()
    # local_map = get_local_map(client, radius=20)
    # print(local_map)
    # result = execute_lua(client, """
    # local pos = game.players[1].position
    # local r = 30
    # local area = {{pos.x - r, pos.y - r}, {pos.x + r, pos.y + r}}
    # local entities = game.surfaces['nauvis'].find_entities_filtered{area=area, force="player"}
    # for _, e in ipairs(entities) do
    #     rcon.print(e.name .. ' at ' .. e.position.x .. ',' .. e.position.y)
    # end
    # """)
    # print(result)
    print(get_local_map(client, radius=15))