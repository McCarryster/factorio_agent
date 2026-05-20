"""
API
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
    TODO: description
    """
    wrapped = _LOAD_WRAPPER.format(lua=_escape_lua_string(lua))
    output = client.send_command("/silent-command " + wrapped) or ""
    return {
        "status": "ERROR" if output.startswith("ERROR:") else "OK",
        "output": output,
    }


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Connecting to Factorio RCON...")
    get_factorio_client()
    print("Connected.\n")