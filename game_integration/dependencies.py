
"""
TODO: description
"""


import factorio_rcon


# Global singleton cache variables
_client: factorio_rcon.RCONClient | None = None

def get_client(host: str, port: int, password: str) -> factorio_rcon.RCONClient:
    global _client

    if _client is None:
        _client = factorio_rcon.RCONClient(host, port, password) # Initialize the client if it doesn't exist
    return _client

