"""
TODO: description
"""

from game_integration.starting_functions import get_player_inventory, get_player_position
from metrics.all_metrics import get_all_metrics
from metrics.skill_library import get_skills_for_prompt
import factorio_rcon
from pathlib import Path
from metrics.reward_t import RewardCalculator


def build_observation(client: factorio_rcon.RCONClient, 
                      result: dict[str, str] | None,
                      reward_calc: RewardCalculator,
                      skills_dir: Path, 
                      start: bool = False) -> str:
    """
    TODO: make description
    """

    inventory = get_player_inventory(client)
    pos = get_player_position(client)
    metrics = get_all_metrics(client, reward_calc, skills_dir=skills_dir)

    core_obs = f"""
INVENTORY: {", ".join([f"{k}: {v}" for k, v in inventory.items()])}.
CHARACTER POSITION: {pos["x"], pos["y"]}.
AVAILABLE SKILLS ({metrics['skill_library_size']} TOTAL):
    {get_skills_for_prompt(skills_dir)}
METRICS:
    reward this step: {metrics['reward']}
    unique items seen: {metrics['unique_items_produced']}
    entities placed: {metrics['entities_placed']}
    technologies researched: {metrics['technologies_researched']}
    technology tree depth: {metrics['tech_tree_depth']}
    skill reuse rate: {metrics['skill_reuse_rate']}
    """

    if start:
        return core_obs

    if result:
        status_line = "ACTION SUCCEEDED" if result["status"] == "OK" else "ACTION FAILED"
        return f"""
RESULT: {result['status']}
OUTPUT: {result['output'] or '(no output)'}
{status_line}
""" + core_obs
    else:
        return core_obs


# if __name__ == "__main__":
#     from game_integration.dependencies import get_client
#     import agent.cfg as cfg

#     factorio_client: factorio_rcon.RCONClient = get_client()

#     obs = build_observation(client=factorio_client, radius=1, result=None, skills_dir=cfg.SKILLS_DIR)
#     print(obs)