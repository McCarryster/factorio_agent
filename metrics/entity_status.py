from game_integration.factorio_bridge import execute_lua

# Factorio 2.x entity status enum values (verified)
STATUS_MAP = {
    1:  "WORKING",
    2:  "NORMAL",
    3:  "GHOST",
    4:  "BROKEN",
    5:  "NOT_PLUGGED_IN_ELECTRIC_NETWORK",
    6:  "NETWORKS_CONNECTED",
    7:  "NETWORKS_DISCONNECTED",
    8:  "CHARGING",
    9:  "DISCHARGING",
    10: "FULLY_CHARGED",
    11: "TURNED_OFF_DURING_DAYTIME",
    12: "CANT_DIVIDE_SEGMENTS",
    13: "NOT_CONNECTED_TO_RAIL",
    14: "LOW_POWER",
    15: "OUT_OF_LOGISTIC_NETWORK",
    16: "WAITING_FOR_PLANTS_TO_GROW",
    17: "NO_SPOT_SEEDABLE_BY_INPUTS",
    18: "NO_INGREDIENTS",
    19: "NO_RECIPE",
    20: "NO_RESEARCH_IN_PROGRESS",
    21: "NO_MINABLE_RESOURCES",
    22: "NOT_CONNECTED_TO_HUB_OR_PAD",
    23: "LOW_INPUT_FLUID",
    24: "NO_INPUT_FLUID",
    25: "FLUID_INGREDIENT_SHORTAGE",
    26: "ITEM_INGREDIENT_SHORTAGE",
    27: "FULL_OUTPUT",
    28: "NOT_ENOUGH_SPACE_IN_OUTPUT",
    29: "FULL_BURNT_RESULT_OUTPUT",
    30: "MISSING_REQUIRED_FLUID",
    31: "MISSING_SCIENCE_PACKS",
    32: "WAITING_FOR_SOURCE_ITEMS",
    33: "WAITING_FOR_MORE_ITEMS",
    34: "WAITING_FOR_SPACE_IN_DESTINATION",
    35: "PREPARING_ROCKET_FOR_LAUNCH",
    36: "WAITING_TO_LAUNCH_ROCKET",
    37: "WAITING_FOR_SPACE_IN_PLATFORM_HUB",
    38: "LAUNCHING_ROCKET",
    39: "THRUST_NOT_REQUIRED",
    40: "ON_THE_WAY",
    41: "WAITING_IN_ORBIT",
    42: "WAITING_AT_STOP",
    43: "WAITING_FOR_ROCKETS_TO_ARRIVE",
    44: "NOT_ENOUGH_THRUST",
    45: "DESTINATION_STOP_FULL",
    46: "NO_PATH",
    47: "NO_MODULES_TO_TRANSMIT",
    48: "RECHARGING_AFTER_POWER_OUTAGE",
    49: "WAITING_FOR_TARGET_TO_BE_BUILT",
    50: "WAITING_FOR_TRAIN",
    51: "NO_AMMO",
    52: "LOW_TEMPERATURE",
    53: "NO_FUEL",
    54: "NO_POWER",
    55: "DISABLED_BY_CONTROL_BEHAVIOR",
    56: "CLOSED_BY_CIRCUIT_NETWORK",
    57: "OPENED_BY_CIRCUIT_NETWORK",
    58: "FROZEN",
    59: "PAUSED",
    60: "DISABLED_BY_SCRIPT",
    61: "DISABLED",
    62: "MARKED_FOR_DECONSTRUCTION",
    63: "COMPUTING_NAVIGATION",
    64: "NO_FILTER",
    65: "PIPELINE_OVEREXTENDED",
    66: "RECIPE_NOT_RESEARCHED",
    67: "RECIPE_IS_PARAMETER",
}

# Statuses that mean the entity is actively broken/blocked
PROBLEM_STATUSES = {
    "NO_FUEL", "NO_POWER", "LOW_POWER", "NOT_PLUGGED_IN_ELECTRIC_NETWORK",
    "NO_RECIPE", "NO_INGREDIENTS", "ITEM_INGREDIENT_SHORTAGE",
    "FLUID_INGREDIENT_SHORTAGE", "NO_INPUT_FLUID", "LOW_INPUT_FLUID",
    "FULL_OUTPUT", "NOT_ENOUGH_SPACE_IN_OUTPUT", "FULL_BURNT_RESULT_OUTPUT",
    "NO_MINABLE_RESOURCES", "BROKEN", "MISSING_REQUIRED_FLUID",
    "WAITING_FOR_SOURCE_ITEMS", "WAITING_FOR_MORE_ITEMS",
    "NETWORKS_DISCONNECTED",
}

# Entity types that use status=-1 (no meaningful machine status) — we override
# the display so the agent sees something useful instead of UNKNOWN(-1).
# Format: entity_name_substring -> display string when status is -1
_NO_STATUS_ENTITIES = {
    "electric-pole":        "CONNECTED",        # assume connected; PROBLEM_STATUSES
    "transport-belt":       "WORKING",           # belts don't use status field
    "underground-belt":     "WORKING",
    "splitter":             "WORKING",
    "pipe":                 "WORKING",
    "pipe-to-ground":       "WORKING",
    "gate":                 "WORKING",
    "wall":                 "WORKING",
    "wooden-chest":         "NORMAL",
    "iron-chest":           "NORMAL",
    "steel-chest":          "NORMAL",
}

def _resolve_status(e: dict) -> str:
    """
    Return a human-readable status string for an entity.

    Electric poles and passive entities report status=-1 in Factorio 2.x.
    We override those with sensible defaults so the agent never sees UNKNOWN(-1).
    """
    raw = e.get("status", -1)
    mapped = STATUS_MAP.get(raw)
    if mapped:
        return mapped

    # raw == -1 (or any unmapped value): check if the entity type has a known default
    name = e.get("name", "")
    for substr, default_status in _NO_STATUS_ENTITIES.items():
        if substr in name:
            return default_status

    # Truly unknown — keep the raw value visible so we can add it to STATUS_MAP
    return f"UNKNOWN({raw})"


_LUA_ENTITY_STATUS = """
local surface = game.surfaces['nauvis']
local entities = surface.find_entities_filtered{force="player"}
local results = {}

for _, e in ipairs(entities) do
    if e.type ~= "character" then
        local products_finished = 0
        if e.type == "furnace" or e.type == "assembling-machine" or e.type == "rocket-silo" then
            products_finished = e.products_finished or 0
        end

        local entry = {
            name      = e.name,
            type      = e.type,
            x         = e.position.x,
            y         = e.position.y,
            direction = e.direction or 0,
            status    = e.status or -1,
            electric_network_id = e.electric_network_id or -1,
            products_finished = products_finished,
            pickup_x  = (e.type == "inserter") and e.pickup_position.x or -1,
            pickup_y  = (e.type == "inserter") and e.pickup_position.y or -1,
            drop_x    = (e.type == "inserter") and e.drop_position.x or -1,
            drop_y    = (e.type == "inserter") and e.drop_position.y or -1,
            fuel = {},
            input = {},
            output = {}
        }

        if e.type == "mining-drill" or e.type == "furnace" or e.type == "boiler" then
            local inv = e.get_inventory(defines.inventory.fuel)
            if inv then
                for _, item in ipairs(inv.get_contents()) do
                    entry.fuel[item.name] = item.count
                end
            end
        end

        if e.type == "furnace" then
            local inv_src = e.get_inventory(defines.inventory.furnace_source)
            if inv_src then
                for _, item in ipairs(inv_src.get_contents()) do
                    entry.input[item.name] = item.count
                end
            end
            local inv_res = e.get_inventory(defines.inventory.furnace_result)
            if inv_res then
                for _, item in ipairs(inv_res.get_contents()) do
                    entry.output[item.name] = item.count
                end
            end
        end

        if e.type == "assembling-machine" then
            local inv_in = e.get_inventory(defines.inventory.assembling_machine_input)
            if inv_in then
                for _, item in ipairs(inv_in.get_contents()) do
                    entry.input[item.name] = item.count
                end
            end
            local inv_out = e.get_inventory(defines.inventory.assembling_machine_output)
            if inv_out then
                for _, item in ipairs(inv_out.get_contents()) do
                    entry.output[item.name] = item.count
                end
            end
        end

        results[#results + 1] = entry
    end
end

rcon.print(helpers.table_to_json(results))
"""

DRILL_OFFSETS = {
    0:  (0, -2),   # north
    4:  (2,  0),   # east
    8:  (0,  2),   # south
    12: (-2, 0),   # west
}

INSERTER_OFFSETS = {
    0:  (0, -1),   # north
    4:  (1,  0),   # east
    8:  (0,  1),   # south
    12: (-1, 0),   # west
}

DIRECTION_NAMES = {0: "NORTH", 4: "EAST", 8: "SOUTH", 12: "WEST"}

# Entity types that get compressed into run-length summaries when they are
# consecutive, same-direction, same-status, and carry no payload.
_COMPRESSIBLE_TYPES = {
    "transport-belt",
    "underground-belt",
    "pipe",
    "pipe-to-ground",
    "small-electric-pole",
    "medium-electric-pole",
    "big-electric-pole",
    "substation",
}


def compute_drill_output_tile(e):
    dx, dy = DRILL_OFFSETS.get(e["direction"], (0, 2))
    return (e["x"] + dx, e["y"] + dy)


def compute_inserter_tiles(e):
    dx, dy = INSERTER_OFFSETS.get(e["direction"], (0, 0))
    pickup = (e["x"] - dx, e["y"] - dy)
    drop   = (e["x"] + dx, e["y"] + dy)
    return pickup, drop


def find_entity_at(entities, tx, ty, radius=1.5):
    for e in entities:
        if abs(e["x"] - tx) < radius and abs(e["y"] - ty) < radius:
            return e["name"]
    return None


IGNORE_ENTITIES = {"crash-site-spaceship"}


def get_entity_status(client) -> list[dict]:
    import json
    from game_integration.factorio_bridge import execute_lua

    result = execute_lua(client, _LUA_ENTITY_STATUS)
    if result["status"] != "OK" or not result["output"]:
        return []
    try:
        entities = json.loads(result["output"])
    except json.JSONDecodeError as ex:
        print("JSON ERROR:", ex)
        return []

    for e in entities:
        e["status"] = _resolve_status(e)

    entities = [e for e in entities if e["name"] not in IGNORE_ENTITIES]
    return entities


def compute_bottlenecks(entities: list[dict]) -> list[str]:
    bottlenecks = []
    has_drill = False
    has_furnace = False
    has_inserter = False
    drill_connected = False

    for e in entities:
        name = e["name"]
        pos = f"({e['x']}, {e['y']})"
        status = e["status"]

        if "mining-drill" in name:
            has_drill = True
            ox, oy = compute_drill_output_tile(e)
            receiver = find_entity_at(entities, ox, oy)
            if receiver and "furnace" in receiver:
                drill_connected = True

        if "furnace" in name:
            has_furnace = True
        if "inserter" in name:
            has_inserter = True

        if status in PROBLEM_STATUSES:
            bottlenecks.append(f"- {name} at {pos}: {status}")

    if not has_drill:
        bottlenecks.append("- No mining drill placed")
    if not has_furnace:
        bottlenecks.append("- No furnace placed")
    if has_drill and has_furnace and not has_inserter and not drill_connected:
        bottlenecks.append("- Drill and furnace exist but no inserter connecting them")

    return bottlenecks


def _is_compressible(e: dict) -> bool:
    """True if this entity can be folded into a run-length summary line."""
    if e["name"] not in _COMPRESSIBLE_TYPES:
        return False
    # Only compress if the entity carries no interesting payload
    if e.get("fuel") or e.get("input") or e.get("output"):
        return False
    return True


def _same_run(a: dict, b: dict) -> bool:
    """True if b continues the same compressible run as a."""
    return (
        a["name"] == b["name"] and
        a["direction"] == b["direction"] and
        a["status"] == b["status"] and
        _is_compressible(b)
    )


def _format_single_entity(e: dict, index: int, entities: list[dict]) -> list[str]:
    """Format one entity as a list of display lines (no trailing newline)."""
    pos = f"({e['x']}, {e['y']})"
    direction_name = DIRECTION_NAMES.get(e["direction"], str(e["direction"]))
    lines = [f"{e['name']} #{index} at {pos} facing {direction_name}"]
    lines.append(f"  status: {e['status']}")

    if e.get("products_finished"):
        lines.append(f"  products_finished: {e['products_finished']}")
    if e.get("fuel"):
        lines.append(f"  fuel: {', '.join(f'{k}:{v}' for k, v in e['fuel'].items())}")
    if e.get("input"):
        lines.append(f"  input: {', '.join(f'{k}:{v}' for k, v in e['input'].items())}")
    if e.get("output"):
        lines.append(f"  output: {', '.join(f'{k}:{v}' for k, v in e['output'].items())}")

    if "mining-drill" in e["name"]:
        ox, oy = compute_drill_output_tile(e)
        receiver = find_entity_at(entities, ox, oy)
        receiver_str = receiver if receiver else "NONE (ore dropping to ground)"
        lines.append(f"  output tile: ({ox}, {oy}) → {receiver_str}")

    if "inserter" in e["name"]:
        (px, py), (dx, dy) = compute_inserter_tiles(e)
        pickup_entity = find_entity_at(entities, px, py) or "empty tile"
        drop_entity   = find_entity_at(entities, dx, dy) or "empty tile"
        lines.append(f"  pickup tile: ({px}, {py}) → {pickup_entity}")
        lines.append(f"  drop tile:   ({dx}, {dy}) → {drop_entity}")

    return lines


def format_entity_status(entities: list[dict]) -> str:
    if not entities:
        return "No player entities found\n"

    lines = []
    i = 0
    entity_index = 1  # running #N counter shown to the agent

    while i < len(entities):
        e = entities[i]

        # --- run-length compression for boring infrastructure ---
        if _is_compressible(e):
            run_start = e
            run_end = e
            run_count = 1
            j = i + 1
            while j < len(entities) and _same_run(e, entities[j]):
                run_end = entities[j]
                run_count += 1
                j += 1

            direction_name = DIRECTION_NAMES.get(e["direction"], str(e["direction"]))
            if run_count == 1:
                lines.extend(_format_single_entity(e, entity_index, entities))
            else:
                start_pos = f"({run_start['x']},{run_start['y']})"
                end_pos   = f"({run_end['x']},{run_end['y']})"
                lines.append(
                    f"{e['name']} ×{run_count} "
                    f"from {start_pos} to {end_pos} "
                    f"facing {direction_name}  "
                    f"status: {e['status']}"
                )

            entity_index += run_count
            i = j

        else:
            lines.extend(_format_single_entity(e, entity_index, entities))
            entity_index += 1
            i += 1

        lines.append("")  # blank line between entries

    # strip trailing blank line
    while lines and lines[-1] == "":
        lines.pop()

    bottlenecks = compute_bottlenecks(entities)
    lines.append("\n=== BOTTLENECKS ===")
    if bottlenecks:
        lines.extend(bottlenecks)
    else:
        lines.append("- None detected")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Ore patch finder
# ---------------------------------------------------------------------------

_LUA_ORE_PATCHES = """
local surface = game.surfaces['nauvis']
local player  = game.players[1]
local pos     = player.position
local results = {}

-- Search radius around player
local radius  = 120

-- Find all resource types
local resource_names = {}
local all_resources = surface.find_entities_filtered{
    type = "resource",
    area = {
        {pos.x - radius, pos.y - radius},
        {pos.x + radius, pos.y + radius}
    }
}

-- Collect unique resource names
local seen = {}
for _, r in ipairs(all_resources) do
    if not seen[r.name] then
        seen[r.name] = true
        table.insert(resource_names, r.name)
    end
end

-- For each resource type, find clusters by looking for dense areas
-- Use a grid approach: divide area into 10x10 cells, find densest cell per resource
for _, rname in ipairs(resource_names) do
    local ores = surface.find_entities_filtered{
        name = rname,
        area = {
            {pos.x - radius, pos.y - radius},
            {pos.x + radius, pos.y + radius}
        }
    }

    if #ores == 0 then goto continue_resource end

    -- Find all tiles of this resource
    -- Group into clusters by proximity (simple: find bounding box of contiguous groups)
    -- For simplicity: find the cell with highest density in a 20x20 grid
    local best_cx, best_cy, best_count = 0, 0, 0
    local cell_size = 15

    -- Sample candidate centers every 10 tiles
    local min_x, min_y = math.huge, math.huge
    local max_x, max_y = -math.huge, -math.huge
    for _, e in ipairs(ores) do
        min_x = math.min(min_x, e.position.x)
        max_x = math.max(max_x, e.position.x)
        min_y = math.min(min_y, e.position.y)
        max_y = math.max(max_y, e.position.y)
    end

    -- Sample grid of candidate centers
    local step = 10
    local sx = min_x
    while sx <= max_x do
        local sy = min_y
        while sy <= max_y do
            -- Count ores in cell_size radius around (sx, sy)
            local nearby = surface.find_entities_filtered{
                name  = rname,
                area  = {
                    {sx - cell_size, sy - cell_size},
                    {sx + cell_size, sy + cell_size}
                }
            }
            if #nearby > best_count then
                -- Compute centroid of this cluster
                local sum_x, sum_y = 0, 0
                for _, e in ipairs(nearby) do
                    sum_x = sum_x + e.position.x
                    sum_y = sum_y + e.position.y
                end
                best_count = #nearby
                best_cx = sum_x / #nearby
                best_cy = sum_y / #nearby
            end
            sy = sy + step
        end
        sx = sx + step
    end

    table.insert(results, {
        name     = rname,
        center_x = best_cx,
        center_y = best_cy,
        count    = #ores,
    })

    ::continue_resource::
end

rcon.print(helpers.table_to_json(results))
"""


def get_ore_patches(client) -> list[dict]:
    """Return list of nearby ore patches with center positions."""
    import json
    result = execute_lua(client, _LUA_ORE_PATCHES)
    if result["status"] != "OK" or not result["output"]:
        return []
    try:
        return json.loads(result["output"])
    except json.JSONDecodeError:
        return []


def format_ore_patches(patches: list[dict]) -> str:
    """Format ore patch summary for the planner."""
    if not patches:
        return "No ore patches found nearby."
    lines = ["=== NEARBY ORE PATCHES ==="]
    for p in patches:
        lines.append(
            f"  {p['name']}: center ({p['center_x']:.1f},{p['center_y']:.1f})"
            f"  ({p['count']} tiles)"
        )
    return "\n".join(lines)


_LUA_FIND_DRILL_POSITION = """
local surface = game.surfaces['nauvis']
local rname   = "{resource_name}"
local near_x  = {near_x}
local near_y  = {near_y}
local search_r = {search_radius}

local ores = surface.find_entities_filtered{{
    name = rname,
    area = {{
        {{near_x - search_r, near_y - search_r}},
        {{near_x + search_r, near_y + search_r}}
    }}
}}

if #ores == 0 then
    rcon.print("NONE")
    return
end

-- Sort ore tiles by distance from hint (closest first)
table.sort(ores, function(a, b)
    local da = (a.position.x-near_x)^2 + (a.position.y-near_y)^2
    local db = (b.position.x-near_x)^2 + (b.position.y-near_y)^2
    return da < db
end)

-- Try the 50 closest ore tiles as candidate drill centers
local checked = {{}}
local best_x, best_y, best_score = 0, 0, -math.huge

for i = 1, math.min(50, #ores) do
    local ore = ores[i]
    local cx = math.floor(ore.position.x + 0.5)
    local cy = math.floor(ore.position.y + 0.5)
    local key = cx..","..cy
    if not checked[key] then
        checked[key] = true

        local target = surface.find_entities_filtered{{
            name = rname,
            area = {{{{cx-1,cy-1}},{{cx+1,cy+1}}}}
        }}
        local others = surface.find_entities_filtered{{
            type = "resource",
            area = {{{{cx-1,cy-1}},{{cx+1,cy+1}}}}
        }}
        -- Score: target tiles good, other resource tiles very bad
        local score = #target * 2 - (#others - #target) * 10

        if score > best_score then
            local can = surface.can_place_entity{{
                name="burner-mining-drill", position={{cx,cy}}, force="player"
            }}
            if can then
                best_score = score
                best_x = cx
                best_y = cy
            end
        end
    end
end

if best_score > -math.huge then
    rcon.print(best_x..","..best_y)
else
    rcon.print("NONE")
end
"""


def find_drill_position(
    client,
    resource_name: str,
    near_x: float,
    near_y: float,
    search_radius: float = 20,
) -> tuple[float, float] | None:
    """
    Find the best position to place a burner-mining-drill on a specific resource,
    minimizing overlap with other resource types.

    Returns (x, y) integer center position, or None if not found.
    """
    from game_integration.factorio_bridge import execute_lua

    lua = _LUA_FIND_DRILL_POSITION.format(
        resource_name=resource_name,
        near_x=near_x,
        near_y=near_y,
        search_radius=search_radius,
    )
    result = execute_lua(client, lua)
    if result["status"] != "OK" or result["output"].strip() == "NONE":
        return None
    parts = result["output"].strip().split(",")
    return float(parts[0]), float(parts[1])