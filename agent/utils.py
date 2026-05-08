"""
TODO: description
"""


from typing import Any
import re


def parse_agent_output(text: str) -> dict[str, Any]:
    """
    Parse <thought>, <action>, <done> from text using regex.

    Args:
        text: Input string

    Returns:
        Dictionary with parsed values
    """

    def extract(tag: str) -> str:
        pattern: str = rf"<{tag}>(.*?)</{tag}>"
        match: re.Match[str] | None = re.search(pattern, text, re.DOTALL)
        return match.group(1).strip() if match else ""

    result: dict[str, Any] = {
        "thought": extract("thought"),
        "action": extract("action"),
        "done": extract("done"),
    }
    result["done"] = result["done"].lower() == "true"
    return result