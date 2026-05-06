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


def build_observation(result: dict[str, str], inventory: dict[str, int], pos: dict[str, float]) -> str:
    """
    TODO: make description
    """

    observation = f"""
        RESULT: {result['status']}
        OUTPUT: {result['output']}
        INVENTORY: {", ".join([f"{k}: {v}" for k, v in inventory.items()])}
        POSITION: {pos["x"], pos["y"]}
    """
    
    return observation



if __name__ == "__main__":
    test_response = """TextBlock(citations=None, text='<thought>\nI need to start playing Factorio from scratch with the goal of launching a rocket. Let me begin by understanding the current game state. I should:\n\n1. First, check what\'s available in my starting inventory\n2. Understand the current map/world state\n3. Identify available resources nearby\n4. Plan the initial steps to progress toward rocket launch\n\nTo launch a rocket, I\'ll eventually need to:\n- Build a rocket silo\n- Produce rocket parts and fuel\n- Research rocket technology\n- But first, I need basic infrastructure: mining, smelting, crafting, and power generation\n\nLet me start by querying the game state to see what I\'m working with.\n</thought>\n\n<action>\n-- Initial game state inspection\nlocal player = game.player\nlocal inventory = player.get_main_inventory()\nlocal position = player.position\n\n-- Log initial state\nlog("=== FACTORIO EXPEDITION START ===")\nlog("Player position: " .. serpent.block(position))\nlog("Inventory contents:")\nfor name, count in pairs(inventory.get_contents()) do\n    log("  " .. name .. ": " .. count)\nend\n\n-- Check technology progress\nlog("Researched technologies:")\nfor tech_name, tech in pairs(game.player_force.technologies) do\n    if tech.researched then\n        log("  " .. tech_name)\n    end\nend\n\n-- Get available recipes\nlog("Available recipes: " .. table.size(game.player_force.recipes))\n\n-- Check for nearby resources\nlog("Scanning nearby map for resources...")\nlocal surface = game.player.surface\nlog("Surface name: " .. surface.name)\n</action>\n\n<done>false</done>', type='text')"""
    res = parse_agent_output(test_response)
    print("THOUGHT", res['thought'])
    print()
    print("ACTION", res['action'])
    print()
    print("DONE", res['done'])