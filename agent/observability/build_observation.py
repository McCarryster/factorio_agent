"""
TODO: description
"""

from game_integration.starting_functions import get_player_inventory, get_player_position, get_nearby_cluster_resources
from metrics.all_metrics import get_all_metrics
import factorio_rcon
from pathlib import Path


def build_observation(client: factorio_rcon.RCONClient, result: dict[str, str] | None, radius: int, skills_dir: Path, start: bool = False) -> str:
    """
    TODO: make description
    """

    inventory = get_player_inventory(client)
    pos = get_player_position(client)
    metrics = get_all_metrics(client, skills_dir=skills_dir)

    core_obs = f"""
INVENTORY: {", ".join([f"{k}: {v}" for k, v in inventory.items()])}.
CHARACTER POSITION: {pos["x"], pos["y"]}.
METRICS:
    reward this step: {metrics['reward']}
    unique items seen: {metrics['unique_items_produced']}
    entities placed: {metrics['entities_placed']}
    technologies researched: {metrics['technologies_researched']}
    technology tree depth: {metrics['tech_tree_depth']}
    skills in library ({metrics['skill_library_size']} total): {metrics['skill_names']}
    skill reuse rate: {metrics['skill_reuse_rate']}
        """

    if start:
        return core_obs

    if result:
        return f"""
RESULT: {result['status']}
OUTPUT: {result['output']}
        """ + core_obs
    else:
        return core_obs


# if __name__ == "__main__":

#     from game_integration.dependencies import get_client
#     from game_integration.factorio_bridge import execute_lua
#     import time

#     client = get_client()


#     task = "Place the burner-mining-drill on an iron ore patch and the stone-furnace next to it. Add coal to both."
#     initial_observation = build_observation(client, result=None, radius=64, skills_dir=skills_dir, start=True)
#     observation = f"TASK: {task}" + initial_observation
#     print(observation)


#     # execute_lua(client, "game.players[1].insert{name='stone-wall', count=2}")
#     # time.sleep(1)
#     print('#'*100)


#     observation_inloop = build_observation(client, result=None, radius=64, start=True)
#     print(observation_inloop)


#     execute_lua(client, "game.players[1].insert{name='stone-wall', count=2}")
#     time.sleep(1)


#     print('#'*100)
#     result = {
#         "status": "OK",
#         "output": "stuff happened",
#     }
#     observation = build_observation(client, result=result, radius=64, start=False)
#     final_observation = f"TASK: {task}" + observation
#     print(final_observation)