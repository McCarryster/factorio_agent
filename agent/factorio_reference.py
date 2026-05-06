factorio_lua_ref = """
FACTORIO 2.x LUA REFERENCE

Output:
  always use rcon.print() never log()

Player:
  local player = game.players[1]
  local pos = player.position  -- {x, y}

Inventory:
  local inv = player.get_main_inventory()
  for _, item in ipairs(inv.get_contents()) do
    -- item.name, item.count, item.quality
  end

Remove inventory from player:
  inv.remove{name="burner-mining-drill", count=1}

Surface:
  local surface = game.surfaces['nauvis']
  local entities = surface.find_entities_filtered{name="iron-ore", position=pos, radius=50}
  local ok = surface.can_place_entity{name="burner-mining-drill", position=pos}
  local entity = surface.create_entity{name="stone-furnace", position=pos, force="player"}

Entity fuel:
  local fuel_inv = entity.get_fuel_inventory()
  fuel_inv.insert{name="coal", count=10}
"""