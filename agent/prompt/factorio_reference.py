FACTORIO_LUA_REF = """
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


Entity placement — ALWAYS remove from inventory after create_entity or placement is invalid:
  local entity = surface.create_entity{name="burner-mining-drill", position=pos, force="player"}
  if entity then
    player.get_main_inventory().remove{name="burner-mining-drill", count=1}
  else
    rcon.print("ERROR: placement failed")
  end

Entity directions and output faces:
  defines.direction.north = 0 (outputs upward)
  defines.direction.east  = 2 (outputs rightward)  
  defines.direction.south = 4 (outputs downward)
  defines.direction.west  = 6 (outputs leftward)

Burner mining drill (2x2 entity):
  - Placed at position (x,y), occupies (x±0.5, y±0.5)
  - Output drops to the tile in front of its output face
  - Example: drill at (24,-124) facing east (direction=2) outputs to (25.5,-124)

Inserter (1x1 entity):
  - Pickup from behind, drops in front
  - Must be placed between source and destination
  - Example: inserter at (25,-124) facing east picks from (24,-124) drops to (26,-124)

Stone furnace (2x2 entity):
  - Input on any face
  - Fuel slot separate from input

Entity power requirements:
  burner-mining-drill: needs fuel (coal/wood) in fuel slot
  electric-mining-drill: needs electricity (power pole within range)
  inserter (yellow): needs electricity
  burner-inserter: needs fuel
  stone-furnace: needs fuel
  electric-furnace: needs electricity
  assembling-machine-1/2/3: needs electricity
  boiler: needs fuel + water input
  steam-engine: needs steam input + connected to power pole
"""