
from game_integration.factorio_bridge import execute_lua

result = execute_lua("rcon.print(tostring(game.player))")
print(result)