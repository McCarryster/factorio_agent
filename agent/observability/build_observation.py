"""
TODO: description
"""

from game_integration.starting_functions import get_player_inventory, get_player_position, get_nearby_cluster_resources
from metrics.all_metrics import get_all_metrics
from metrics.milestones import get_entities_placed
from metrics.skill_library import get_skills_for_prompt, get_skill_library_size
from metrics.technologies import get_tech_tree_depth, get_technologies_researched
import factorio_rcon
from pathlib import Path
from metrics.reward_t import RewardCalculator
from typing import Any


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


def get_world_state(client: factorio_rcon.RCONClient, radius: int, skills_dir: Path) -> str:
    """
    TODO: description
    """
    inventory: dict[str, int] = get_player_inventory(client)
    resources: list[dict[str, Any]] = get_nearby_cluster_resources(client, radius=radius)
    pos: dict[str, float] = get_player_position(client)
    entities_placed: dict[str, int] = get_entities_placed(client=client)
    tech_tree_depth: int = get_tech_tree_depth(client=client)
    technologies_researched: list[str] = get_technologies_researched(client=client)
    library_size: int = get_skill_library_size(skills_dir=skills_dir)

    core_state = f"""
CURRENT WORLD STATE:
    INVENTORY: {", ".join([f"{k}: {v}" for k, v in inventory.items()])}.
    CHARACTER POSITION: {pos["x"], pos["y"]}.
    NEARBY CLUSTERS OF RESOURCES: {", ".join([f"{r['name']} at ({r['x']}, {r['y']}) amount={r['amount']}, tiles={r['count']}" for r in resources])}.
    ENTITIES PLACED: {entities_placed}.
    TECHNOLOGIES RESEARCHED ({tech_tree_depth} TOTAL): {technologies_researched}

AVAILABLE SKILLS ({library_size} TOTAL):
    {get_skills_for_prompt(skills_dir)}
"""
    
    return core_state


if __name__ == "__main__":
    from game_integration.dependencies import get_client
    import agent.cfg as cfg

    factorio_client: factorio_rcon.RCONClient = get_client()

    obs = get_world_state(client=factorio_client, radius=64, skills_dir=cfg.SKILLS_DIR)
    print(obs)