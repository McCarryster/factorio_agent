"""
TODO: description
"""


from metrics.reward_t import get_reward
from metrics.unique_items import get_unique_items_produced
from metrics.skill_library import get_skill_library_size, get_skill_names
from metrics.skill_reuse import get_skill_reuse_rate
from metrics.technologies import get_tech_tree_depth, get_technologies_researched
from metrics.milestones import get_entities_placed
from typing import Any
import factorio_rcon
from pathlib import Path


# def get_all_metrics() -> dict[str, Any]:
#     """
#     TODO: description
#     """

#     # reward = get_reward()
#     # print(reward)
#     # from game_integration.factorio_bridge import execute_lua
#     # execute_lua("game.players[1].insert{name='stone-wall', count=2}")
#     # reward = get_reward()
#     # print("2", reward)
#     # return ""

#     return {
#     "reward": get_reward(),
#     "unique_items_produced": get_unique_items_produced(),
#     "skill_library_size": get_skill_library_size(),
#     "skill_reuse_rate": get_skill_reuse_rate(),
#     "technologies_researched": get_technologies_researched(),
#     "tech_tree_depth": get_tech_tree_depth(),
#     "entities_placed": get_entities_placed()
# }


def get_all_metrics(client: factorio_rcon.RCONClient, skills_dir: Path) -> dict[str, Any]:
    """
    TODO: description
    """
    total, _ = get_reward(client)

    return {
        "reward": total,
        "unique_items_produced": get_unique_items_produced(),
        "skill_library_size": get_skill_library_size(skills_dir),
        "skill_names": get_skill_names(skills_dir),
        "skill_reuse_rate": get_skill_reuse_rate(),
        "technologies_researched": get_technologies_researched(client),
        "tech_tree_depth": get_tech_tree_depth(client),
        "entities_placed": get_entities_placed(client)
    }

if __name__ == "__main__":
    from game_integration.dependencies import get_client
    from game_integration.factorio_bridge import execute_lua

    client = get_client()
    
    metrics = get_all_metrics(client)
    print(f"metrics 1: {metrics}")
    
    execute_lua(client, "game.players[1].insert{name='stone-wall', count=2}")
    print('#'*100)
    
    metrics = get_all_metrics(client)
    print(metrics)