"""
basic_operations.py

Primitive operations for the Factorio agent.

Each primitive returns a Result dict:
  {
    "success": bool,
    "message": str,
    "data":    dict,   # structured data (positions, entities, etc.)
  }

Coordinate convention:
  All coordinates are GAME coordinates (what get_entities reports).
  - 1x1 entities (belt, inserter, pole, pipe): centers at X.5, Y.5
  - 2x2 entities (drill, furnace, chest): centers at integer X, Y
  - Larger entities: see Factorio docs

  When placing, pass the intended center — Factorio snaps to the correct grid.
  When referring to an entity, use the coordinates get_entities reports.

Direction is specified via (output_to_x, output_to_y):
  Instead of "north/east/south/west", pass the coordinates where the entity
  should output. The primitive computes the cardinal direction from the vector.
  For entities that don't care about direction, omit these parameters.
"""

from __future__ import annotations
import time

from game_integration.factorio_bridge import execute_lua


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NORTH = 0
EAST  = 4
SOUTH = 8
WEST  = 12

MAX_MINING_COUNT      = 20
MAX_WAIT_SECONDS      = 30
MAX_WAIT_ITEM_TIMEOUT = 120


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok(message: str = "ok", **data) -> dict:
    return {"success": True, "message": message, "data": data}

def _fail(message: str, **data) -> dict:
    return {"success": False, "message": message, "data": data}

def _run(client, lua: str) -> str:
    result = execute_lua(client, lua)
    return (result.get("output") or "").strip()

def _direction_from_coords(entity_x: float, entity_y: float,
                            output_x: float, output_y: float) -> int:
    """Compute cardinal direction from entity center to output target."""
    dx = output_x - entity_x
    dy = output_y - entity_y
    if abs(dx) >= abs(dy):
        return EAST if dx > 0 else WEST
    return SOUTH if dy > 0 else NORTH


# ---------------------------------------------------------------------------
# 1. find_ore_patch
# ---------------------------------------------------------------------------

def find_ore_patch(client, resource: str,
                   near_x: float = 0, near_y: float = 0,
                   search_radius: float = 150,
                   max_results: int = 5) -> dict:
    """
    Find positions of resource patches near (near_x, near_y).

    resource: "iron-ore" | "coal" | "copper-ore" | "stone"

    Returns data: {"positions": [{"x", "y", "amount"}, ...]}
    Sorted by distance from (near_x, near_y).
    """
    lua = f"""
local surface = game.players[1].surface
local near_x, near_y = {near_x}, {near_y}

local ores = surface.find_entities_filtered{{
    name="{resource}",
    area={{{{near_x-{search_radius}, near_y-{search_radius}}},
          {{near_x+{search_radius}, near_y+{search_radius}}}}}
}}

local seen = {{}}
local candidates = {{}}
for i, ore in ipairs(ores) do
    if i % 10 == 0 then
        local cx = math.floor(ore.position.x / 10) * 10
        local cy = math.floor(ore.position.y / 10) * 10
        local key = cx..","..cy
        if not seen[key] then
            seen[key] = true
            local dist = (ore.position.x-near_x)^2 + (ore.position.y-near_y)^2
            table.insert(candidates, {{
                x=ore.position.x, y=ore.position.y,
                dist=dist, amount=ore.amount
            }})
        end
    end
end

table.sort(candidates, function(a,b) return a.dist < b.dist end)
local results = {{}}
for i=1, math.min({max_results}, #candidates) do
    local c = candidates[i]
    table.insert(results, c.x..","..c.y..","..c.amount)
end
if #results == 0 then rcon.print("NONE")
else rcon.print(table.concat(results, "|")) end
"""
    out = _run(client, lua)
    if out == "NONE" or not out:
        return _fail(f"no {resource} found within {search_radius} of ({near_x},{near_y})",
                     positions=[])

    positions = []
    for part in out.split("|"):
        bits = part.split(",")
        if len(bits) >= 3:
            positions.append({
                "x":      float(bits[0]),
                "y":      float(bits[1]),
                "amount": int(bits[2]),
            })
    return _ok(f"found {len(positions)} {resource} patches", positions=positions)


# ---------------------------------------------------------------------------
# 2. find_valid_pump_position
# ---------------------------------------------------------------------------

def find_valid_pump_position(client,
                              near_x: float = 0, near_y: float = 0,
                              search_radius: float = 200,
                              max_results: int = 3) -> dict:
    """
    Find water positions where an offshore pump can be placed AND has room
    for boiler + steam engine on the adjacent land.

    Returns data: {"positions": [{"x", "y", "facing_x", "facing_y"}, ...]}
    where facing_(x,y) is where the pump's output goes (toward land).
    """
    lua = f"""
local surface = game.players[1].surface
local near_x, near_y = {near_x}, {near_y}
local results = {{}}

-- Try each direction for offshore pump
local dirs = {{
    {{name="EAST",  dx=1,  dy=0,  d=defines.direction.east}},
    {{name="WEST",  dx=-1, dy=0,  d=defines.direction.west}},
    {{name="SOUTH", dx=0,  dy=1,  d=defines.direction.south}},
    {{name="NORTH", dx=0,  dy=-1, d=defines.direction.north}},
}}

for dx_offset = -{int(search_radius)}, {int(search_radius)}, 3 do
    for dy_offset = -{int(search_radius)}, {int(search_radius)}, 3 do
        local x = near_x + dx_offset
        local y = near_y + dy_offset
        local tile = surface.get_tile(x, y)
        if tile and (tile.name == "water" or tile.name == "deepwater") then
            for _, d in ipairs(dirs) do
                local can_pump = surface.can_place_entity{{
                    name="offshore-pump", position={{x,y}},
                    force="player", direction=d.d
                }}
                local pipe_x = x + d.dx * 2
                local pipe_y = y + d.dy * 2
                local can_pipe = surface.can_place_entity{{
                    name="pipe", position={{pipe_x, pipe_y}}, force="player"
                }}
                local boiler_x = x + d.dx * 4
                local boiler_y = y + d.dy * 4
                local can_boiler = surface.can_place_entity{{
                    name="boiler", position={{boiler_x, boiler_y}}, force="player"
                }}
                if can_pump and can_pipe and can_boiler then
                    local dist = dx_offset*dx_offset + dy_offset*dy_offset
                    table.insert(results, {{
                        x=x, y=y,
                        facing_x=x + d.dx, facing_y=y + d.dy,
                        dist=dist
                    }})
                    break
                end
            end
        end
    end
end

table.sort(results, function(a,b) return a.dist < b.dist end)
local out = {{}}
for i=1, math.min({max_results}, #results) do
    local r = results[i]
    table.insert(out, r.x..","..r.y..","..r.facing_x..","..r.facing_y)
end
if #out == 0 then rcon.print("NONE")
else rcon.print(table.concat(out, "|")) end
"""
    out = _run(client, lua)
    if out == "NONE" or not out:
        return _fail("no valid pump position found", positions=[])

    positions = []
    for part in out.split("|"):
        bits = part.split(",")
        if len(bits) >= 4:
            positions.append({
                "x":        float(bits[0]),
                "y":        float(bits[1]),
                "facing_x": float(bits[2]),
                "facing_y": float(bits[3]),
            })
    return _ok(f"found {len(positions)} pump positions", positions=positions)


# ---------------------------------------------------------------------------
# 3. get_entities
# ---------------------------------------------------------------------------

def get_entities(client,
                 near_x: float | None = None,
                 near_y: float | None = None,
                 radius: float | None = None) -> dict:
    """
    Get all player-built entities (or filter by area).

    Each entity has:
      name, x, y, status,
      fuel:   {item: count}
      input:  {item: count}
      output: {item: count}
      facing_x, facing_y:  coordinates where the entity is facing (or null)
      drop_x, drop_y:      where this entity drops items (or null)
      pickup_x, pickup_y:  where this entity picks up items from (or null)

    Returns data: {"entities": [...]}
    """
    area_clause = ""
    if near_x is not None and near_y is not None and radius is not None:
        area_clause = (f"area={{{{{near_x - radius},{near_y - radius}}},"
                       f"{{{near_x + radius},{near_y + radius}}}}}, ")

    lua = f"""
local surface = game.players[1].surface
local ents = surface.find_entities_filtered{{{area_clause}force="player"}}
local lines = {{}}

local status_names = {{
    [defines.entity_status.working] = "WORKING",
    [defines.entity_status.normal] = "WORKING",
    [defines.entity_status.full_output] = "FULL_OUTPUT",
    [defines.entity_status.no_fuel] = "NO_FUEL",
    [defines.entity_status.no_ingredients] = "NO_INGREDIENTS",
    [defines.entity_status.waiting_for_space_in_destination] = "WAITING_FOR_SPACE",
    [defines.entity_status.no_power] = "NO_POWER",
    [defines.entity_status.networks_connected] = "WORKING",
    [defines.entity_status.disabled_by_control_behavior] = "DISABLED",
}}

local function inv_to_string(inv)
    if not inv then return "" end
    local parts = {{}}
    for _, item in ipairs(inv.get_contents()) do
        -- Factorio 2.x: count may be a number or quality table
        local c = item.count
        if type(c) == "table" then c = c.count or 0 end
        table.insert(parts, item.name.."="..tostring(c))
    end
    return table.concat(parts, ";")
end

for _, e in ipairs(ents) do
    if e.type ~= "character" then
        local ok, err = pcall(function()
        local status = (e.status and status_names[e.status]) or "UNKNOWN"
        local fuel   = inv_to_string(e.get_inventory(defines.inventory.fuel))
        local input  = ""
        for _, inv_id in ipairs({{defines.inventory.furnace_source,
                                    defines.inventory.assembling_machine_input}}) do
            local s = inv_to_string(e.get_inventory(inv_id))
            if s ~= "" then input = s; break end
        end
        local output = ""
        for _, inv_id in ipairs({{defines.inventory.furnace_result,
                                    defines.inventory.assembling_machine_output,
                                    defines.inventory.chest,
                                    defines.inventory.item_main}}) do
            local s = inv_to_string(e.get_inventory(inv_id))
            if s ~= "" then output = s; break end
        end

        -- Direction-related coordinates (only inserters have pickup/drop positions)
        local fx, fy = "", ""
        local dx, dy = "", ""
        local px, py = "", ""

        if e.type == "inserter" then
            if e.drop_position then
                dx = tostring(e.drop_position.x)
                dy = tostring(e.drop_position.y)
                fx = dx; fy = dy
            end
            if e.pickup_position then
                px = tostring(e.pickup_position.x)
                py = tostring(e.pickup_position.y)
                if fx == "" then fx = px; fy = py end
            end
        elseif e.drop_position then
            -- mining drills and some other entities have drop_position
            dx = tostring(e.drop_position.x)
            dy = tostring(e.drop_position.y)
            fx = dx; fy = dy
        end

        table.insert(lines, e.name.."|"..
            string.format("%.1f", e.position.x).."|"..
            string.format("%.1f", e.position.y).."|"..
            status.."|"..fuel.."|"..input.."|"..output.."|"..
            fx.."|"..fy.."|"..dx.."|"..dy.."|"..px.."|"..py)
        end)
        if not ok then
            table.insert(lines, "ERROR|0.0|0.0|"..tostring(err).."|||||||||")
        end
    end
end
if #lines == 0 then rcon.print("NONE")
else rcon.print(table.concat(lines, "||")) end
"""
    out = _run(client, lua)
    if out == "NONE" or not out:
        return _ok("no entities", entities=[])

    def parse_inv(s):
        if not s:
            return {}
        return {part.split("=")[0]: int(part.split("=")[1])
                for part in s.split(";") if "=" in part}

    def parse_float(s):
        try:
            return float(s) if s else None
        except ValueError:
            return None

    entities = []
    for line in out.split("||"):
        bits = line.split("|")
        if len(bits) < 13:
            continue
        if bits[0] == "ERROR":
            # Lua error on this entity — print for debugging
            import sys
            print(f"  [get_entities Lua error] {bits[3]}", file=sys.stderr)
            continue
        entities.append({
            "name":     bits[0],
            "x":        float(bits[1]),
            "y":        float(bits[2]),
            "status":   bits[3],
            "fuel":     parse_inv(bits[4]),
            "input":    parse_inv(bits[5]),
            "output":   parse_inv(bits[6]),
            "facing_x": parse_float(bits[7]),
            "facing_y": parse_float(bits[8]),
            "drop_x":   parse_float(bits[9]),
            "drop_y":   parse_float(bits[10]),
            "pickup_x": parse_float(bits[11]),
            "pickup_y": parse_float(bits[12]),
        })
    return _ok(f"found {len(entities)} entities", entities=entities)


# ---------------------------------------------------------------------------
# 4. get_inventory
# ---------------------------------------------------------------------------

def get_inventory(client) -> dict:
    """
    Get the player's inventory contents.

    Returns data: {"inventory": {item_name: count, ...}}
    """
    lua = """
local inv = game.players[1].get_main_inventory()
local parts = {}
for _, item in ipairs(inv.get_contents()) do
    table.insert(parts, item.name.."="..item.count)
end
rcon.print(table.concat(parts, "|"))
"""
    out = _run(client, lua)
    if not out:
        return _ok("inventory empty", inventory={})

    inv = {}
    for part in out.split("|"):
        if "=" in part:
            name, count = part.rsplit("=", 1)
            inv[name] = int(count)
    return _ok(f"{len(inv)} item types in inventory", inventory=inv)


# ---------------------------------------------------------------------------
# 5. mine_resource
# ---------------------------------------------------------------------------

def mine_resource(client, resource: str,
                  near_x: float = 0, near_y: float = 0,
                  count: int = 20) -> dict:
    """
    Manually mine up to `count` of `resource` from the nearest patch.
    Capped at MAX_MINING_COUNT (20). Items go to player inventory.

    resource: "iron-ore" | "coal" | "copper-ore" | "stone"
    """
    count = min(max(1, count), MAX_MINING_COUNT)

    lua = f"""
local surface = game.players[1].surface
local player = game.players[1]
local inv = player.get_main_inventory()
local resource_name = "{resource}"
local near_x = {near_x}
local near_y = {near_y}
local target_count = {count}

-- Find nearest resource entity of correct type
local found = surface.find_entities_filtered{{
    name=resource_name,
    area={{{{near_x-100, near_y-100}}, {{near_x+100, near_y+100}}}}
}}
if #found == 0 then
    rcon.print("FAIL:no " .. resource_name .. " found near (" .. near_x .. "," .. near_y .. ")")
    return
end

-- Sort by distance, pick closest
local best = found[1]
local best_dist = (best.position.x-near_x)^2 + (best.position.y-near_y)^2
for i=2, #found do
    local d = (found[i].position.x-near_x)^2 + (found[i].position.y-near_y)^2
    if d < best_dist then best = found[i]; best_dist = d end
end

local mined = 0
local proto = prototypes.entity[best.name]
local products = (proto.mineable_properties and proto.mineable_properties.products) or {{}}

-- Keep mining until count reached or patch depleted
while mined < target_count do
    if not best.valid or best.amount <= 0 then
        local nearby = surface.find_entities_filtered{{
            name=resource_name,
            area={{{{best.position.x-5, best.position.y-5}},
                  {{best.position.x+5, best.position.y+5}}}}
        }}
        if #nearby == 0 then break end
        best = nearby[1]
    end
    best.amount = best.amount - 1
    for _, prod in ipairs(products) do
        inv.insert{{name=prod.name, count=prod.amount or 1}}
    end
    mined = mined + 1
end

rcon.print("OK:mined " .. mined .. "x " .. resource_name)
"""
    out = _run(client, lua)
    if out.startswith("OK:"):
        time.sleep(count * 0.3)  # simulate mining time
        return _ok(out[3:], mined=count)
    return _fail(out[5:] if out.startswith("FAIL:") else out, mined=0)


# ---------------------------------------------------------------------------
# 6. chop_tree
# ---------------------------------------------------------------------------

def chop_tree(client, near_x: float = 0, near_y: float = 0,
              search_radius: float = 50) -> dict:
    """
    Chop the nearest tree near (near_x, near_y). One tree per call.
    Wood goes to player inventory.
    """
    lua = f"""
local surface = game.players[1].surface
local player = game.players[1]
local trees = surface.find_entities_filtered{{
    type="tree",
    area={{{{{near_x}-{search_radius},{near_y}-{search_radius}}},
          {{{near_x}+{search_radius},{near_y}+{search_radius}}}}}
}}
if #trees == 0 then
    rcon.print("FAIL:no trees within " .. {search_radius} .. " of (" .. {near_x} .. "," .. {near_y} .. ")")
    return
end

-- Find nearest tree
local best, best_dist = trees[1], (trees[1].position.x-{near_x})^2 + (trees[1].position.y-{near_y})^2
for i=2, #trees do
    local d = (trees[i].position.x-{near_x})^2 + (trees[i].position.y-{near_y})^2
    if d < best_dist then best = trees[i]; best_dist = d end
end

local tx, ty = best.position.x, best.position.y
local mined = player.mine_entity(best, true)
if mined then
    rcon.print("OK:chopped tree at (" .. tx .. "," .. ty .. ")")
else
    rcon.print("FAIL:could not chop tree at (" .. tx .. "," .. ty .. ")")
end
"""
    out = _run(client, lua)
    if out.startswith("OK:"):
        time.sleep(1)
        return _ok(out[3:])
    return _fail(out[5:] if out.startswith("FAIL:") else out)


# ---------------------------------------------------------------------------
# 7. craft_item
# ---------------------------------------------------------------------------

def craft_item(client, name: str, count: int = 1) -> dict:
    """
    Instantly craft `count` of `name`.
    Checks: recipe is enabled, inventory has all ingredients.
    Deducts ingredients, adds products to inventory.
    """
    if count <= 0:
        return _fail("count must be positive")

    lua = f"""
local player = game.players[1]
local force = player.force
local inv = player.get_main_inventory()

local recipe = force.recipes["{name}"]
if not recipe then
    rcon.print("FAIL:no recipe for {name}")
    return
end
if not recipe.enabled then
    rcon.print("FAIL:recipe {name} is locked")
    return
end

local proto = prototypes.recipe["{name}"]
local count = {count}

for _, ing in ipairs(proto.ingredients) do
    local needed = ing.amount * count
    if inv.get_item_count(ing.name) < needed then
        rcon.print("FAIL:need " .. needed .. " " .. ing.name .. " but have " .. inv.get_item_count(ing.name))
        return
    end
end

for _, ing in ipairs(proto.ingredients) do
    inv.remove{{name=ing.name, count=ing.amount * count}}
end

local produced = {{}}
for _, prod in ipairs(proto.products) do
    local amt = (prod.amount or prod.amount_min or 1) * count
    inv.insert{{name=prod.name, count=amt}}
    table.insert(produced, amt .. "x " .. prod.name)
end

rcon.print("OK:" .. table.concat(produced, ", "))
"""
    out = _run(client, lua)
    if out.startswith("OK:"):
        return _ok(f"crafted {out[3:]}")
    return _fail(out[5:] if out.startswith("FAIL:") else out)


# ---------------------------------------------------------------------------
# 8. insert_item
# ---------------------------------------------------------------------------

def insert_item(client, entity_x: float, entity_y: float,
                item: str, count: int) -> dict:
    """
    Insert items from player inventory into the entity at (entity_x, entity_y).
    Works for fuel slots (coal into drill/furnace), input slots (ore into furnace),
    chests, etc. Tries fuel inventory first, then input, then chest.
    """
    if count <= 0:
        return _fail("count must be positive")

    lua = f"""
local player = game.players[1]
local surface = player.surface
local player_inv = player.get_main_inventory()

local have = player_inv.get_item_count("{item}")
local to_insert = math.min(have, {count})
if to_insert == 0 then
    rcon.print("FAIL:no {item} in inventory")
    return
end

-- Find entity at position
local ents = surface.find_entities_filtered{{
    position={{{entity_x},{entity_y}}}, radius=1.5, force=player.force
}}
local entity = nil
for _, e in ipairs(ents) do
    if e.type ~= "character" then entity = e; break end
end
if not entity then
    rcon.print("FAIL:no entity at (" .. {entity_x} .. "," .. {entity_y} .. ")")
    return
end

-- Try different inventories
local inserted = 0
for _, inv_type in ipairs({{
    defines.inventory.fuel,
    defines.inventory.furnace_source,
    defines.inventory.assembling_machine_input,
    defines.inventory.chest,
}}) do
    local entity_inv = entity.get_inventory(inv_type)
    if entity_inv and entity_inv.can_insert{{name="{item}", count=to_insert}} then
        inserted = entity_inv.insert{{name="{item}", count=to_insert}}
        if inserted > 0 then
            player_inv.remove{{name="{item}", count=inserted}}
            break
        end
    end
end

if inserted > 0 then
    rcon.print("OK:inserted " .. inserted .. "x {item} into " .. entity.name)
else
    rcon.print("FAIL:could not insert {item} into " .. entity.name)
end
"""
    out = _run(client, lua)
    if out.startswith("OK:"):
        return _ok(out[3:])
    return _fail(out[5:] if out.startswith("FAIL:") else out)


# ---------------------------------------------------------------------------
# 9. take_item
# ---------------------------------------------------------------------------

def take_item(client, entity_x: float, entity_y: float,
              item: str, count: int) -> dict:
    """
    Take items from the entity at (entity_x, entity_y) into player inventory.
    Checks output inventory first, then chest, then fuel.
    """
    if count <= 0:
        return _fail("count must be positive")

    lua = f"""
local player = game.players[1]
local surface = player.surface
local inv = player.get_main_inventory()

local ents = surface.find_entities_filtered{{
    position={{{entity_x},{entity_y}}}, radius=1.5, force=player.force
}}
local entity = nil
for _, e in ipairs(ents) do
    if e.type ~= "character" then entity = e; break end
end
if not entity then
    rcon.print("FAIL:no entity at (" .. {entity_x} .. "," .. {entity_y} .. ")")
    return
end

local taken = 0
for _, inv_type in ipairs({{
    defines.inventory.furnace_result,
    defines.inventory.assembling_machine_output,
    defines.inventory.chest,
    defines.inventory.item_main,
    defines.inventory.fuel,
}}) do
    local entity_inv = entity.get_inventory(inv_type)
    if entity_inv then
        local available = entity_inv.get_item_count("{item}")
        if available > 0 then
            local take = math.min(available, {count} - taken)
            entity_inv.remove{{name="{item}", count=take}}
            inv.insert{{name="{item}", count=take}}
            taken = taken + take
            if taken >= {count} then break end
        end
    end
end

if taken > 0 then
    rcon.print("OK:took " .. taken .. "x {item} from " .. entity.name)
else
    rcon.print("FAIL:no {item} found in " .. entity.name)
end
"""
    out = _run(client, lua)
    if out.startswith("OK:"):
        return _ok(out[3:])
    return _fail(out[5:] if out.startswith("FAIL:") else out)


# ---------------------------------------------------------------------------
# 10. place_entity
# ---------------------------------------------------------------------------

def place_entity(client, name: str, x: float, y: float,
                 output_to_x: float | None = None,
                 output_to_y: float | None = None) -> dict:
    """
    Place entity from player inventory at (x, y).
    Checks can_place_entity before placing.
    Automatically snaps 2x2 entities to integer coords, 1x1 to .5 coords.

    If output_to_(x,y) is given, direction is computed so the entity outputs
    toward that point. For entities without meaningful direction, omit these.
    Returns actual placed position (what Factorio reports after placing).

    Returns data: {"placed_at": {"x", "y"}}
    """
    # 2x2 entities need integer centers; 1x1 entities need .5 centers
    TWO_BY_TWO = {
        "burner-mining-drill", "electric-mining-drill", "stone-furnace",
        "steel-furnace", "electric-furnace", "wooden-chest", "iron-chest",
        "steel-chest", "assembling-machine-1", "assembling-machine-2",
        "assembling-machine-3", "lab", "radar", "accumulator",
    }
    if name in TWO_BY_TWO:
        sx = float(round(x))
        sy = float(round(y))
    else:
        sx = round(x * 2) / 2
        sy = round(y * 2) / 2

    if output_to_x is not None and output_to_y is not None:
        direction = _direction_from_coords(sx, sy, output_to_x, output_to_y)
    else:
        direction = NORTH

    lua = f"""
local player = game.players[1]
local surface = player.surface
local px = {sx}
local py = {sy}
local dir = {direction}

if player.get_main_inventory().get_item_count("{name}") == 0 then
    rcon.print("FAIL:no {name} in inventory")
    return
end

local can = surface.can_place_entity{{
    name="{name}", position={{px, py}}, direction=dir, force=player.force
}}
if not can then
    rcon.print("FAIL:cannot place {name} at (" .. px .. "," .. py .. ") - blocked or invalid")
    return
end

local entity = surface.create_entity{{
    name="{name}", position={{px, py}}, direction=dir,
    force=player.force, player=player, raise_built=true
}}
if entity and entity.valid then
    player.get_main_inventory().remove{{name="{name}", count=1}}
    -- Verify it still exists (scenario scripts may destroy it)
    local verify = surface.find_entity("{name}", {{entity.position.x, entity.position.y}})
    if verify and verify.valid then
        rcon.print("OK:" .. entity.position.x .. "," .. entity.position.y)
    else
        rcon.print("FAIL:entity was placed but immediately destroyed (scenario conflict?)")
    end
else
    rcon.print("FAIL:create_entity returned nil")
end
"""
    out = _run(client, lua)
    if out.startswith("OK:"):
        coords = out[3:].split(",")
        return _ok(f"placed {name}",
                   placed_at={"x": float(coords[0]), "y": float(coords[1])})
    return _fail(out[5:] if out.startswith("FAIL:") else out)


# ---------------------------------------------------------------------------
# 11. remove_entity
# ---------------------------------------------------------------------------

def remove_entity(client, x: float, y: float) -> dict:
    """
    Mine the entity at (x, y) back into the player's inventory.
    """
    lua = f"""
local player = game.players[1]
local surface = player.surface

local ents = surface.find_entities_filtered{{
    position={{{x},{y}}}, radius=1.5, force=player.force
}}
local entity = nil
for _, e in ipairs(ents) do
    if e.type ~= "character" then entity = e; break end
end
if not entity then
    rcon.print("FAIL:no entity at (" .. {x} .. "," .. {y} .. ")")
    return
end

local name = entity.name
local pos = entity.position
local ok = player.mine_entity(entity, true)
if ok then
    rcon.print("OK:removed " .. name .. " from (" .. pos.x .. "," .. pos.y .. ")")
else
    rcon.print("FAIL:could not mine " .. name)
end
"""
    out = _run(client, lua)
    if out.startswith("OK:"):
        return _ok(out[3:])
    return _fail(out[5:] if out.startswith("FAIL:") else out)


# ---------------------------------------------------------------------------
# 12. rotate_entity
# ---------------------------------------------------------------------------

def rotate_entity(client, x: float, y: float,
                  output_to_x: float, output_to_y: float) -> dict:
    """
    Rotate the entity at (x, y) so it outputs toward (output_to_x, output_to_y).
    """
    direction = _direction_from_coords(x, y, output_to_x, output_to_y)

    lua = f"""
local player = game.players[1]
local surface = player.surface

local ents = surface.find_entities_filtered{{
    position={{{x},{y}}}, radius=1.5, force=player.force
}}
local entity = nil
for _, e in ipairs(ents) do
    if e.type ~= "character" then entity = e; break end
end
if not entity then
    rcon.print("FAIL:no entity at (" .. {x} .. "," .. {y} .. ")")
    return
end

entity.direction = {direction}
rcon.print("OK:rotated " .. entity.name .. " direction=" .. {direction})
"""
    out = _run(client, lua)
    if out.startswith("OK:"):
        return _ok(out[3:])
    return _fail(out[5:] if out.startswith("FAIL:") else out)


# ---------------------------------------------------------------------------
# 13. wait_seconds
# ---------------------------------------------------------------------------

def wait_seconds(client, seconds: int) -> dict:
    """
    Sleep N seconds. Capped at MAX_WAIT_SECONDS (30).
    Used to let furnaces smelt, drills mine, etc.
    """
    seconds = min(max(1, seconds), MAX_WAIT_SECONDS)
    time.sleep(seconds)
    return _ok(f"waited {seconds}s")


# ---------------------------------------------------------------------------
# 14. wait_for_item
# ---------------------------------------------------------------------------

def wait_for_item(client, entity_x: float, entity_y: float,
                  item: str, min_count: int,
                  timeout_seconds: int = 60) -> dict:
    """
    Wait until the entity at (entity_x, entity_y) has at least min_count of item.
    Polls every 2 seconds. timeout capped at MAX_WAIT_ITEM_TIMEOUT (120).
    """
    timeout_seconds = min(max(1, timeout_seconds), MAX_WAIT_ITEM_TIMEOUT)
    deadline = time.time() + timeout_seconds

    lua = f"""
local surface = game.players[1].surface
local ents = surface.find_entities_filtered{{
    position={{{entity_x},{entity_y}}}, radius=1.5, force=game.players[1].force
}}
local total = 0
for _, e in ipairs(ents) do
    if e.type ~= "character" then
        for i = 1, 5 do
            local inv = e.get_inventory(i)
            if inv then total = total + inv.get_item_count("{item}") end
        end
        break
    end
end
rcon.print(total)
"""
    last_count = 0
    while time.time() < deadline:
        out = _run(client, lua)
        try:
            current = int(out.strip())
            last_count = current
            if current >= min_count:
                return _ok(f"entity has {current}x {item}", count=current)
        except ValueError:
            pass
        time.sleep(2)

    return _fail(f"timeout: entity at ({entity_x},{entity_y}) has {last_count}x "
                 f"{item}, needed {min_count} within {timeout_seconds}s",
                 count=last_count)