"""
TODO: description
"""

from game_integration.starting_functions import get_player_inventory, get_player_position, get_nearby_cluster_resources, get_local_map
from metrics.skill_library import get_skills_for_prompt, get_skill_library_size, get_skill_names
from game_integration.factorio_bridge import execute_lua
import json
from metrics.technologies import get_technologies_researched
from metrics.entity_status import get_entity_status, format_entity_status
from metrics.production_tracker import ProductionTracker

import factorio_rcon
from pathlib import Path
from typing import Any
# from agent.observability.build_planner_context import build_planner_context
# from agent.observability.build_planner_context import build_planner_context
from agent.observability.world_model import build_semantic_world_model


def build_observation(client,
                      result: dict | None,
                      skills_dir: Path,
                      production_tracker: ProductionTracker,
                      current_task: str = "",
                      start: bool = False) -> str:

    inventory = get_player_inventory(client)
    entities = get_entity_status(client)
    production_tracker.update(entities)

    sections = []

    # task
    if current_task:
        sections.append(f"=== CURRENT TASK ===\n{current_task}")

    # factory state + bottlenecks (already combined in format_entity_status)
    sections.append(format_entity_status(entities))

    # production metrics
    sections.append(production_tracker.format_throughput(entities))

    # inventory
    inv_str = ", ".join(f"{k}: {v}" for k, v in inventory.items()) or "empty"
    sections.append(f"=== INVENTORY ===\n{inv_str}")

    techs_result = execute_lua(client, """
    local techs = {}
    for name, tech in pairs(game.players[1].force.technologies) do
        if tech.researched then
            techs[#techs+1] = name
        end
    end
    rcon.print(helpers.table_to_json(techs))
    """)
    researched = []
    if techs_result["status"] == "OK" and techs_result["output"]:
        try:
            researched = json.loads(techs_result["output"])
        except:
            pass
    tech_str = ", ".join(researched) if researched else "none"
    sections.append(f"=== RESEARCHED TECHNOLOGIES ===\n{tech_str}")

    # skills
    skill_names = get_skill_names(skills_dir)
    sections.append(f"=== AVAILABLE SKILLS ({len(skill_names)}) ===\n" + "\n".join(f"- {s}" for s in skill_names))

    # last action result
    if not start and result:
        status_line = "ACTION SUCCEEDED" if result["status"] == "OK" else "ACTION FAILED"
        sections.append(f"=== LAST ACTION ===\n{status_line}\n{result['output'] or '(no output)'}")

    return "\n\n".join(sections)


# def build_planner_context(client: factorio_rcon.RCONClient, 
#                           production_tracker: ProductionTracker,
#                           skills_dir: Path, 
#                           current_task: str = "") -> str:

#     inventory = get_player_inventory(client)
#     entities = get_entity_status(client)
#     production_tracker.update(entities)

#     sections = []

#     # task
#     if current_task:
#         sections.append(f"=== CURRENT TASK ===\n{current_task}")

#     # factory state + bottlenecks (already combined in format_entity_status)
#     sections.append(format_entity_status(entities))

#     # production metrics
#     prod_metrics = production_tracker.format_throughput(entities)
#     if "  (rate tracking: need 2+ observations)" in prod_metrics:
#         pass
#     else:
#         sections.append(prod_metrics)

#     # inventory
#     inv_str = ", ".join(f"{k}: {v}" for k, v in inventory.items()) or "empty"
#     sections.append(f"=== INVENTORY ===\n{inv_str}")

#     techs = get_technologies_researched(client)
#     sections.append(f"=== RESEARCHED TECHNOLOGIES ===\n{techs}")

#     # skills
#     skill_names = get_skill_names(skills_dir)
#     sections.append(f"=== AVAILABLE SKILLS ({len(skill_names)}) ===\n" + "\n".join(f"- {s}" for s in skill_names))

#     return "\n\n".join(sections)



# def get_planner_context(client: factorio_rcon.RCONClient, 
#                           production_tracker: ProductionTracker,
#                           skills_dir: Path, 
#                           current_task: str = "") -> str:

#     entities: list[dict] = get_entity_status(client)
#     production_tracker.update(entities)
#     throughput: dict[str, float] = production_tracker.get_throughput()
#     inventory: dict[str, int] = get_player_inventory(client)
#     techs: str = get_technologies_researched(client)

#     planner_context: str = build_planner_context(current_task, entities, throughput, inventory, techs)

#     return planner_context

def get_planner_context(
    client: factorio_rcon.RCONClient,
    production_tracker: ProductionTracker,
    skills_dir: Path,
    current_task: str = "",
    current_requirement: str = "",
) -> str:

    entities: list[dict] = get_entity_status(client)
    production_tracker.update(entities)
    throughput: dict[str, float] = production_tracker.get_throughput()
    inventory: dict[str, int] = get_player_inventory(client)
    techs: list[str] = get_technologies_researched(client)

    return build_semantic_world_model(
        raw_entities=entities,
        throughput=throughput,
        inventory=inventory,
        technologies=techs,
        goal=current_task,
        current_requirement=current_requirement,
    )



# def get_world_state(client: factorio_rcon.RCONClient, radius: int, skills_dir: Path) -> str:
#     """
#     TODO: description
#     """
#     inventory: dict[str, int] = get_player_inventory(client)
#     resources: list[dict[str, Any]] = get_nearby_cluster_resources(client, radius=radius)
#     pos: dict[str, float] = get_player_position(client)
#     entities_placed: dict[str, int] = get_entities_placed(client=client)
#     tech_tree_depth: int = get_tech_tree_depth(client=client)
#     technologies_researched: list[str] = get_technologies_researched(client=client)
#     library_size: int = get_skill_library_size(skills_dir=skills_dir)

#     core_state = f"""
# CURRENT WORLD STATE:
#     INVENTORY: {", ".join([f"{k}: {v}" for k, v in inventory.items()])}.
#     CHARACTER POSITION: {pos["x"], pos["y"]}.
#     NEARBY CLUSTERS OF RESOURCES: {", ".join([f"{r['name']} at ({r['x']}, {r['y']}) amount={r['amount']}, tiles={r['count']}" for r in resources])}.
#     ENTITIES PLACED: {entities_placed}.
#     TECHNOLOGIES RESEARCHED ({tech_tree_depth} TOTAL): {technologies_researched}

# AVAILABLE SKILLS ({library_size} TOTAL):
#     {get_skills_for_prompt(skills_dir)}
# """
    
#     return core_state


if __name__ == "__main__":
    from game_integration.dependencies import get_factorio_client
    import agent.cfg as cfg
    import time

    factorio_client = get_factorio_client()
    production_tracker = ProductionTracker()

    # # first observation
    # obs = build_observation(client=factorio_client, result=None, skills_dir=cfg.SKILLS_DIR, 
    #                         production_tracker=production_tracker, current_task="task example...")
    # # print("=== FIRST OBSERVATION ===")
    # print(obs)

    # time.sleep(5)  # wait for furnace to produce something

    # # second observation — now rate will show
    # obs = build_observation(client=factorio_client, result=None, skills_dir=cfg.SKILLS_DIR,
    #                         production_tracker=production_tracker, current_task="task example...")
    # print("=== SECOND OBSERVATION ===")
    # print(obs)

    context_planner = get_planner_context(factorio_client, production_tracker, cfg.SKILLS_DIR, current_task="task example...")
    print(context_planner)