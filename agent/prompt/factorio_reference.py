FACTORIO_LUA_REF = """
FACTORIO 2.x LUA REFERENCE

Output:
  always use rcon.print() never log()

Player:
  local player = game.players[1]
  local pos = player.position  -- {x, y}

Player movement:
  player.position is READ-ONLY — cannot teleport player
  To get coal: use surface.find_entities_filtered{name="coal"} to find location,
  then insert directly into entity fuel inventory using entity.get_inventory(defines.inventory.fuel)
  Players do not need to physically move to interact with entities via Lua

Inventory:
  local inv = player.get_main_inventory()
  for _, item in ipairs(inv.get_contents()) do
    -- item.name, item.count, item.quality
  end

Remove inventory from player:
  inv.remove{name="burner-mining-drill", count=1}

Crafting items:
  player.begin_crafting{recipe="wooden-chest", count=1}
  player.begin_crafting{recipe="burner-inserter", count=1}
  player.begin_crafting{recipe="burner-mining-drill", count=1}
  player.begin_crafting{recipe="iron-gear-wheel", count=1}
  
  Note: begin_crafting returns count of items queued (0 if not enough materials)
  Crafting is instantaneous in Lua — items appear in inventory immediately
  
Crafting recipes (early game):
  wooden-chest:        2 wood
  iron-gear-wheel:     2 iron-plate
  burner-inserter:     1 iron-plate + 1 iron-gear-wheel
  burner-mining-drill: 3 iron-plate + 3 iron-gear-wheel + 3 stone
  stone-furnace:       5 stone
  transport-belt:      1 iron-plate + 1 iron-gear-wheel (makes 2)

Check if craftable:
  player.get_craftable_count("wooden-chest")  -- returns how many you can craft

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

Entity status check:
  entity.status returns an integer — compare against status codes
  1 = WORKING, 53 = NO_FUEL, 54 = NO_POWER, 18 = NO_INGREDIENTS
  DO NOT use entity.working — this property doesn't exist in 2.x
  DO NOT use entity.get_status() — this method doesn't exist in 2.x

Entity power requirements — choose the right entity:
  burner-inserter: needs fuel (coal/wood) — NO research required, craftable from start
  inserter (yellow): needs electricity — requires basic-inserter technology research
  burner-mining-drill: needs fuel — NO research required
  electric-mining-drill: needs electricity — requires electric-mining technology research
  stone-furnace: needs fuel — NO research required
  electric-furnace: needs electricity — requires advanced research
  RULE: If technology is not in === AVAILABLE TECHNOLOGIES ===, you cannot use electric entities.
  RULE: Always prefer burner variants early game when no power infrastructure exists.

Entity spacing for inserters:
  Inserter is 1x1. It needs 1 free tile between source and destination.
  Two 2x2 entities placed adjacent (e.g. furnace at y=2 and drill at y=4) have NO gap — inserter cannot fit.
  To connect two 2x2 entities with an inserter, they must be placed 3 tiles apart (center-to-center).
  Example: drill at (43, 5) and furnace at (43, 2) — gap tile at y=3.5, inserter fits at (43, 3).
  When placing entities you intend to connect later, always leave a 1-tile gap between them.

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