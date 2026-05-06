# from agent.prompt import get_factorio_voyager_prompt
from agent.prompt import PROMPT
import anthropic
from anthropic.types import MessageParam
from agent.starting_functions import get_player_inventory, get_player_position
from agent.utils import parse_agent_output, build_observation
import agent.cfg as cfg
from game_integration.factorio_bridge import execute_lua
from typing import Any


client = anthropic.Anthropic(api_key=cfg.API_KEY)

def run(task: str, max_iterations: int = 5):
    history: list[MessageParam] = [{"role": "user", "content": f"Task: {task}"}]
    for i in range(max_iterations):
        # 1. call API
        # 2. extract code from response
        # 3. execute code
        # 4. observe result
        # 5. update history
        # 6. check if done

        # 1. Call API
        response = client.messages.create(
            model=cfg.DEFAULT_MODEL,
            max_tokens=1024,
            system=PROMPT,
            messages=history
        )
        # response = """TextBlock(citations=None, text='<thought>\nI need to start playing Factorio from scratch with the goal of launching a rocket. Let me begin by understanding the current game state. I should:\n\n1. First, check what\'s available in my starting inventory\n2. Understand the current map/world state\n3. Identify available resources nearby\n4. Plan the initial steps to progress toward rocket launch\n\nTo launch a rocket, I\'ll eventually need to:\n- Build a rocket silo\n- Produce rocket parts and fuel\n- Research rocket technology\n- But first, I need basic infrastructure: mining, smelting, crafting, and power generation\n\nLet me start by querying the game state to see what I\'m working with.\n</thought>\n\n<action>\n-- Initial game state inspection\nlocal player = game.player\nlocal inventory = player.get_main_inventory()\nlocal position = player.position\n\n-- Log initial state\nlog("=== FACTORIO EXPEDITION START ===")\nlog("Player position: " .. serpent.block(position))\nlog("Inventory contents:")\nfor name, count in pairs(inventory.get_contents()) do\n    log("  " .. name .. ": " .. count)\nend\n\n-- Check technology progress\nlog("Researched technologies:")\nfor tech_name, tech in pairs(game.player_force.technologies) do\n    if tech.researched then\n        log("  " .. tech_name)\n    end\nend\n\n-- Get available recipes\nlog("Available recipes: " .. table.size(game.player_force.recipes))\n\n-- Check for nearby resources\nlog("Scanning nearby map for resources...")\nlocal surface = game.player.surface\nlog("Surface name: " .. surface.name)\n</action>\n\n<done>false</done>', type='text')"""

        
        # 2. extract code from response
        parsed_response: dict[str, Any]= parse_agent_output(response.content[0].text) # type: ignore
        # parsed_response: dict[str, Any]= parse_agent_output(response) # type: ignore
        
        # 3. execute code
        result = execute_lua(parsed_response['action'])
        
        # 4. observe result
        current_inventory = get_player_inventory()
        current_pos = get_player_position()
        observation = build_observation(result, current_inventory, current_pos)
        print(observation)

        # 5. update history
        # history.append({"role": "assistant", "content": response})
        history.append({"role": "assistant", "content": response.content[0].text}) # type: ignore
        history.append({"role": "user", "content": observation})

        print(f"Iteration {i} - \n", f"history - {history}")

        if parsed_response["done"]:
            print("Task was marked as DONE")
            break


if __name__ == "__main__":
    run("Place the burner-mining-drill on an iron ore patch and the stone-furnace next to it. Add coal to both.")