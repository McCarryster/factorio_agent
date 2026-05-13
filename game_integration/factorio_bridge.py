"""
factorio_bridge.py — RCON bridge between Python and a Factorio headless server.

Public API
----------
connect()                  — establish module-level RCON connection
execute_lua(lua)           — run Lua inside pcall; returns output, "OK", or "ERROR: ..."
is_error(result)           — True if execute_lua returned an error string
"""

import factorio_rcon
from game_integration.dependencies import get_factorio_client



_LOAD_WRAPPER = (
    'local __fn,__lerr=load("{lua}") '
    "if __fn==nil then rcon.print('ERROR: '..tostring(__lerr)) "
    "else local __ok,__err=pcall(__fn) "
    "if not __ok then rcon.print('ERROR: '..tostring(__err)) end end"
)


def _escape_lua_string(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
         .replace('"', '\\"')
         .replace("\n", "\\n")
         .replace("\r", "\\r")
         .replace("\0", "\\0")
    )


def execute_lua(client: factorio_rcon.RCONClient, lua: str) -> dict[str, str]:
    """
    Execute Lua code on the server with full error capture.

    The code is compiled at runtime via load() so both syntax errors and
    runtime errors are caught and returned as "ERROR: <message>" rather
    than being silently swallowed. Use rcon.print() to produce output.

    Args:
        lua: Lua source code string.
        client: RCON client. Uses module-level connection if omitted.

    Returns:
        {"status": "OK"|"ERROR", "output": <rcon.print output or error string>}
        output is "" when nothing was printed and no error occurred.

    Example:
        result = execute_lua("rcon.print(game.tick)")
    """
    wrapped = _LOAD_WRAPPER.format(lua=_escape_lua_string(lua))
    output = client.send_command("/silent-command " + wrapped) or ""
    return {
        "status": "ERROR" if output.startswith("ERROR:") else "OK",
        "output": output,
    }


# def is_error(result: dict[str, str]) -> bool:
#     """Return True if execute_lua returned an error result."""
#     return result["status"] == "ERROR"


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Connecting to Factorio RCON...")
    get_factorio_client()
    print("Connected.\n")