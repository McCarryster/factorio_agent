"""
TODO: description
"""


import anthropic
import agent.cfg as cfg


# Global singleton cache variables
_anthropic_client: anthropic.Anthropic = anthropic.Anthropic(api_key=cfg.API_KEY)

def get_anthropic_client() -> anthropic.Anthropic:
    global _anthropic_client

    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(api_key=cfg.API_KEY) # Initialize the client if it doesn't exist
    return _anthropic_client