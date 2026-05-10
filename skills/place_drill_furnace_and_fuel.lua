local player = game.players[1]
local surface = game.surfaces['nauvis']
local pos = player.position

-- Find iron ore patches nearby
local ore_entities = surface.find_entities_filtered{name="iron-ore", position=pos, radius=50}
rcon.print("Found " .. #ore_entities .. " iron ore deposits nearby")

if #ore_entities == 0 then
  rcon.print("ERROR: No iron ore patches found")
  return
end

-- Use the first iron ore patch found
local ore_pos = ore_entities[1].position
rcon.print("Target ore position: " .. ore_pos.x .. ", " .. ore_pos.y)

-- Try to place burner-mining-drill on the ore patch
local drill_placed = surface.create_entity{name="burner-mining-drill", position=ore_pos, force="player"}
if drill_placed then
  rcon.print("✓ Burner-mining-drill placed at " .. ore_pos.x .. ", " .. ore_pos.y)
  player.get_main_inventory().remove{name="burner-mining-drill", count=1}
  rcon.print("✓ Removed drill from inventory")
else
  rcon.print("ERROR: Failed to place burner-mining-drill")
  return
end

-- Place stone-furnace adjacent (offset by 2 tiles)
local furnace_pos = {x = ore_pos.x + 2, y = ore_pos.y}
local furnace_placed = surface.create_entity{name="stone-furnace", position=furnace_pos, force="player"}
if furnace_placed then
  rcon.print("✓ Stone-furnace placed at " .. furnace_pos.x .. ", " .. furnace_pos.y)
  player.get_main_inventory().remove{name="stone-furnace", count=1}
  rcon.print("✓ Removed furnace from inventory")
else
  rcon.print("ERROR: Failed to place stone-furnace")
  return
end

-- Add fuel to burner-mining-drill
local drill_fuel_inv = drill_placed.get_fuel_inventory()
local wood_inserted_drill = drill_fuel_inv.insert{name="wood", count=10}
player.get_main_inventory().remove{name="wood", count=wood_inserted_drill}
rcon.print("✓ Added " .. wood_inserted_drill .. " wood to burner-mining-drill")

-- Add fuel to stone-furnace
local furnace_fuel_inv = furnace_placed.get_fuel_inventory()
local wood_inserted_furnace = furnace_fuel_inv.insert{name="wood", count=10}
player.get_main_inventory().remove{name="wood", count=wood_inserted_furnace}
rcon.print("✓ Added " .. wood_inserted_furnace .. " wood to stone-furnace")

-- Final inventory check
local inv = player.get_main_inventory()
local contents = inv.get_contents()
rcon.print("Final inventory:")
for _, item in ipairs(contents) do
  rcon.print("  " .. item.name .. ": " .. item.count)
end