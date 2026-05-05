"""
factorio_bridge.py — RCON bridge between Python and a Factorio headless server.

Public API
----------
connect()                  — establish module-level RCON connection
execute_lua(lua)           — run arbitrary Lua; return rcon.print() output
get_player_position()      — {"x": float, "y": float}
get_player_inventory()     — {"item-name": count, ...}
get_nearby_resources()     — [{"name": str, "x": float, "y": float, "amount": int}, ...]
"""

import json
import cfg as cfg
import factorio_rcon


_client: factorio_rcon.RCONClient | None = None


def connect(
    host: str = cfg.HOST,
    port: int = cfg.PORT,
    password: str = cfg.PASSWORD,
) -> factorio_rcon.RCONClient:
    """
    Connect to the Factorio RCON server and store the connection globally.

    Args:
        host: RCON server hostname or IP.
        port: RCON port.
        password: RCON password.

    Returns:
        The RCONClient (also stored in the module-level _client).
    """
    global _client
    _client = factorio_rcon.RCONClient(host, port, password)
    return _client


def _get_client(client: factorio_rcon.RCONClient | None) -> factorio_rcon.RCONClient:
    if client is not None:
        return client
    if _client is None:
        connect()
    return _client  # type: ignore[return-value]


def execute_lua(lua: str, client: factorio_rcon.RCONClient | None = None) -> str | None:
    """
    Execute arbitrary Lua code on the server.

    Use rcon.print() inside the Lua code to produce a return value.
    Multiple statements can be separated by semicolons.

    Args:
        lua: Lua source code string.
        client: RCON client. Uses module-level connection if omitted.

    Returns:
        String output from rcon.print(), or None if nothing was printed.

    Example:
        result = execute_lua("rcon.print(game.tick)")
    """
    return _get_client(client).send_command("/c " + lua)


# ---------------------------------------------------------------------------
# Observation functions
# ---------------------------------------------------------------------------

def get_player_position(
    player_index: int = 1,
    client: factorio_rcon.RCONClient | None = None,
) -> dict:
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
    return json.loads(result) if result else {}


def get_player_inventory(
    player_index: int = 1,
    client: factorio_rcon.RCONClient | None = None,
) -> dict:
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
    if not result:
        return {}
    counts: dict[str, int] = {}
    for item in json.loads(result):
        counts[item["name"]] = counts.get(item["name"], 0) + item["count"]
    return counts


def get_nearby_resources(
    radius: int = 32,
    player_index: int = 1,
    client: factorio_rcon.RCONClient | None = None,
) -> list:
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
    return json.loads(result) if result else []


# ---------------------------------------------------------------------------
# Quick smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Connecting to Factorio RCON...")
    connect()
    print("Connected.\n")

    pos = get_player_position()
    print(f"Player position : {pos}")

    inv = get_player_inventory()
    print(f"Player inventory: {inv}")

    resources = get_nearby_resources(radius=512)
    print(f"Nearby resources: {len(resources)} entities found")
    for r in resources[:5]:
        print(f"  {r['name']:20s}  at ({r['x']:8.1f}, {r['y']:8.1f})  amount={r['amount']}")
