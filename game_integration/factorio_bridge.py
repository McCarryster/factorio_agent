"""
factorio_bridge.py — RCON bridge between Python and a Factorio headless server.

Public API
----------
connect()                  — establish module-level RCON connection
execute_lua(lua)           — run Lua inside pcall; returns output, "OK", or "ERROR: ..."
is_error(result)           — True if execute_lua returned an error string
"""

import game_integration.cfg as cfg
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


def execute_lua(lua: str, client: factorio_rcon.RCONClient | None = None) -> dict[str, str]:
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
        if is_error(result):
            print("Lua error:", result["output"])
    """
    wrapped = _LOAD_WRAPPER.format(lua=_escape_lua_string(lua))
    output = _get_client(client).send_command("/c " + wrapped) or ""
    return {
        "status": "ERROR" if output.startswith("ERROR:") else "OK",
        "output": output,
    }


def is_error(result: dict[str, str]) -> bool:
    """Return True if execute_lua returned an error result."""
    return result["status"] == "ERROR"


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Connecting to Factorio RCON...")
    connect()
    print("Connected.\n")