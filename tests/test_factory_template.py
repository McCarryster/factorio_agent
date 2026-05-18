from game_integration.dependencies import get_factorio_client
from game_integration.factorio_bridge import execute_lua
from game_integration.primitives import validate_actions, translate_to_lua, parse_execution_output
from agent.factory_template import generate_factory_actions
from metrics.entity_status import find_drill_position
from agent.factory_template import generate_factory_actions

client = get_factorio_client()


# Find actual drill positions on pure resource tiles
iron_pos = (65.0, 2.0)
coal_pos = (94.0, -2.0)

actions = generate_factory_actions(
    iron_ore_center=(64.5, 1.0),
    coal_center=(94.0, -2.0),
    iron_drill_position=iron_pos,
    coal_drill_position=coal_pos,
)

print(f"Total actions: {len(actions)}")
lua = translate_to_lua(actions, max_actions=200)
result = execute_lua(client, lua)
report = parse_execution_output(result["output"])
print(report.summary())