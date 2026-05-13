"""
TODO: description
"""


import factorio_rcon
import game_integration.cfg as cfg

# Global singleton cache variables
_client: factorio_rcon.RCONClient | None = None

def get_factorio_client(host: str = cfg.HOST, port: int = cfg.PORT, password: str = cfg.PASSWORD) -> factorio_rcon.RCONClient:
    global _client

    if _client is None:
        _client = factorio_rcon.RCONClient(host, port, password) # Initialize the client if it doesn't exist
    return _client