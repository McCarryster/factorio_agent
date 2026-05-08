from game_integration.factorio_bridge import execute_lua
from game_integration.dependencies import get_client

factorio_client = get_client()

# Check what's in inventory before
result = execute_lua(factorio_client, """
local player = game.players[1]
local inv = player.get_main_inventory()
rcon.print("Before: " .. serpent.block(inv.get_contents()))
inv.remove{name="burner-mining-drill", count=1}
rcon.print("After: " .. serpent.block(inv.get_contents()))
""")
print(result)