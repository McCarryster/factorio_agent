"""
factory_blocks.py

Composable factory building blocks. Each function returns a list of
primitive action dicts ready for translate_to_lua().

Available blocks:
  place_coal_mining(anchor, num_drills) → coal drills + collector belt + output
  place_iron_mining(anchor, num_drills) → iron drills on ore patch
  place_smelting(anchor, num_furnaces)  → furnaces + inserters + output belt

Coordinate convention:
  All positions are TEMPLATE coordinates (what you pass to create_entity).
  Factorio snaps 1x1 entities (belts, inserters) to nearest 0.5 grid.
  Factorio snaps 2x2 entities (drills, furnaces) to nearest integer.
  
  For belts: template integer x → game x+0.5 (e.g. pass 74 → game places at 74.5)
  For 2x2:   template float → game rounds to nearest integer

Reference geometry (from verified in-game layout):
  Coal drill facing SOUTH at (dx, dy):
    - body occupies (dx±1, dy±1)
    - drop position ≈ (dx+0.5, dy+1.3) → collector belt at y = dy+1.5 (game dy+2)

  Output belt at y = dy - 1.5 (north of drills)
  Connector turn column at x = leftmost_drill_x - 3
  Self-feed inserter: picks output belt, feeds first drill
  Chain inserters: drill→drill, facing WEST
"""

from dataclasses import dataclass

NORTH = 0
EAST  = 4
SOUTH = 8
WEST  = 12
DIR_NAMES = {NORTH:"NORTH", EAST:"EAST", SOUTH:"SOUTH", WEST:"WEST"}


def _snap1(v: float) -> float:
    """Snap to 0.5 grid."""
    return round(v * 2) / 2

def _snap2(v: float) -> float:
    """Snap 2x2 entity to integer (Factorio rounds half-up)."""
    return float(int(v + 0.5) if v >= 0 else -int(-v + 0.5))


def _place(entity, x, y, direction) -> dict:
    return {
        "type":        "PLACE_ENTITY",
        "entity_name": entity,
        "x":           float(x),
        "y":           float(y),
        "direction":   DIR_NAMES[direction],
    }

def _insert(x, y, item, count) -> dict:
    return {
        "type":      "INSERT_ITEM",
        "entity_x":  float(x),
        "entity_y":  float(y),
        "item_name": item,
        "count":     int(count),
    }


@dataclass
class CoalMiningOutput:
    """Describes where coal exits from a coal mining block."""
    belt_x: float        # x position of output belt end (leftmost tile)
    belt_y: float        # y position of output belt
    belt_dir: int        # direction items flow on output belt


import math

def place_coal_mining(
    drill_x: float,
    drill_y: float,
    num_drills: int = 2,
    seed_coal: int = 10,
    output_direction: int = WEST,
) -> tuple[list[dict], CoalMiningOutput]:
    """
    Place a coal mining block: burner drills + collector belt + output.

    Reference layout (verified in game):
      Drills face SOUTH, spaced 3 tiles apart on X axis
      Collector belt runs WEST below drills at y = drill_y + 1.5 (game)
      Turn column at x = leftmost_drill_x - 2
      Output belt runs WEST at y = drill_y - 1.5 (game)
      Self-feed inserter: output belt → first (leftmost) drill
      Chain inserter: drill[i] → drill[i-1] (right to left)

    Args:
        drill_x: x center of FIRST (leftmost) drill (integer, 2x2 entity)
        drill_y: y center of drills (integer, 2x2 entity)
        num_drills: number of coal drills (default 2)
        seed_coal: initial coal to insert into first drill
        output_direction: direction output belt flows (default WEST)

    Returns:
        (actions, output) where output describes the output belt position
    """
    actions = []

    dx = _snap2(drill_x)
    dy = _snap2(drill_y)

    # Drill positions: spaced 3 apart, leftmost at dx
    drill_xs = [dx + i * 3 for i in range(num_drills)]

    # Key y positions (template coords → game adds 0.5)
    collector_ty = int(dy) + 1    # collector belt template y → game dy+1.5
    output_ty    = int(dy) - 1    # template y → game y = dy-0.5
    turn_tx      = int(dx) - 3    # turn column template x → game dx-2.5 (verified)

    # -----------------------------------------------------------------------
    # 1. Drills facing SOUTH
    # -----------------------------------------------------------------------
    for ddx in drill_xs:
        actions.append(_place("burner-mining-drill", ddx, dy, SOUTH))

    # -----------------------------------------------------------------------
    # 2. Collector belt: WEST along bottom of drills
    #    From rightmost drill to turn column
    # -----------------------------------------------------------------------
    for x in range(int(max(drill_xs)), int(turn_tx), -1):  # from rightmost drill to turn
        actions.append(_place("transport-belt", float(x), float(collector_ty), WEST))

    # -----------------------------------------------------------------------
    # 3. Turn column: NORTH from collector to output level
    #    From collector_ty to output_ty (exclusive, items flow naturally)
    # -----------------------------------------------------------------------
    for y in range(int(collector_ty), int(output_ty), -1):  # turn tiles from collector down to output
        actions.append(_place("transport-belt", float(turn_tx), float(y), NORTH))

    # -----------------------------------------------------------------------
    # 4. Output belt: runs in output_direction from turn column
    # -----------------------------------------------------------------------
    # Output belt at output_ty, starts one tile from turn column
    # For WEST output: runs west from turn_tx-1
    if output_direction == WEST:
        # 2 tiles west of turn column (matching reference)
        output_start_x = turn_tx
        output_end_x   = turn_tx - 1
        for x in range(int(output_start_x), int(output_end_x) - 1, -1):
            actions.append(_place("transport-belt", float(x), float(output_ty), WEST))
        output_exit_x = float(output_end_x)
    else:
        # EAST output
        output_start_x = turn_tx + 1
        output_end_x   = turn_tx + 2
        for x in range(int(output_start_x), int(output_end_x) + 1):
            actions.append(_place("transport-belt", float(x), float(output_ty), EAST))
        output_exit_x = float(output_end_x)

    # -----------------------------------------------------------------------
    # 5. Self-feed inserter: picks from turn column belt, feeds first drill
    #    Sits at (turn_tx+1.5, collector_ty) facing WEST
    #    pickup=(turn_tx+0.5, collector_ty)=turn belt, drop=(turn_tx+2.5)=drill side
    # -----------------------------------------------------------------------
    # Self-feed: one tile right of turn column, at DRILL level (not collector level)
    # picks from turn column belt at drill level, drops into first drill body
    sf_x = turn_tx + 1  # template x → game x+0.5
    ins_ty = collector_ty - 1  # drill level (one tile above collector belt)
    actions.append(_place("inserter", float(sf_x), float(ins_ty), WEST))

    # -----------------------------------------------------------------------
    # 6. Chain inserters: drill[i] → drill[i-1], right to left
    #    Inserter at midpoint between drills, facing WEST
    # -----------------------------------------------------------------------
    for i in range(1, num_drills):
        right_x = drill_xs[i]
        left_x  = drill_xs[i - 1]
        chain_x = math.floor((right_x + left_x) / 2)  # floor gives correct game position
        ins_ty = collector_ty - 1  # drill level
        actions.append(_place("inserter", chain_x, float(ins_ty), WEST))

    # -----------------------------------------------------------------------
    # 7. Seed fuel into all drills
    #    Chain inserters will sustain fuel once powered, but need initial coal
    # -----------------------------------------------------------------------
    for ddx in drill_xs:
        actions.append(_insert(ddx, dy, "coal", seed_coal))

    output = CoalMiningOutput(
        belt_x   = output_exit_x,
        belt_y   = float(output_ty) + 0.5,  # game position
        belt_dir = output_direction,
    )
    return actions, output


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------


@dataclass
class IronMiningOutput:
    """Describes where iron ore exits from an iron mining block."""
    belt_x: float        # x of leftmost belt tile (game)
    belt_y: float        # y of belt (game)
    belt_dir: int        # direction items flow


def place_iron_mining(
    drill_x: float,
    drill_y: float,
    num_drills: int = 2,
    seed_coal: int = 5,
) -> tuple[list[dict], IronMiningOutput]:
    """
    Place an iron ore mining block: burner drills facing NORTH + coal feed.

    Reference layout (verified in game):
      Drills face NORTH, spaced 3 apart, drop ore NORTH onto main belt
      Main belt at game y = drill_y - 0.5, runs WEST from rightmost drill
      Turn tile NORTH at x = rightmost_drill_x + 1 (game +1.5)
      Coal feed inserter: picks turn belt, drops rightmost drill
      Chain inserters: picks right drill, drops left drill (facing EAST)

    Args:
        drill_x:   x center of LEFTMOST drill (integer)
        drill_y:   y center of drills (integer, on iron ore patch)
        num_drills: number of iron drills
        seed_coal:  initial coal for fuel

    Returns:
        (actions, output) where output describes the ore output belt
    """
    actions = []

    dx = int(_snap2(drill_x))
    dy = int(_snap2(drill_y))

    # Drill positions: leftmost at dx, spaced 3 apart eastward
    drill_xs = [dx + i * 3 for i in range(num_drills)]

    # Key positions
    belt_ty  = dy - 2        # template y → game dy-1.5 (drill output level)
    turn_tx  = max(drill_xs) + 2   # turn column template x → game rightmost_drill+2.5
    feed_tx  = max(drill_xs) + 2   # feed inserter same column as turn

    # -----------------------------------------------------------------------
    # 1. Drills facing NORTH
    # -----------------------------------------------------------------------
    for ddx in drill_xs:
        actions.append(_place("burner-mining-drill", float(ddx), float(dy), NORTH))

    # -----------------------------------------------------------------------
    # 2. Main belt: WEST from turn column to past leftmost drill
    # -----------------------------------------------------------------------
    for x in range(turn_tx, dx - 2, -1):
        actions.append(_place("transport-belt", float(x), float(belt_ty), WEST))

    # -----------------------------------------------------------------------
    # 3. Turn tile: NORTH at turn column (coal comes from south, feeds belt)
    # -----------------------------------------------------------------------
    actions.append(_place("transport-belt", float(turn_tx), float(belt_ty + 1), NORTH))

    # -----------------------------------------------------------------------
    # 4. Coal feed inserter: picks turn belt, drops rightmost drill
    #    At (turn_tx - 0.5, belt_ty + 1) facing EAST
    #    pickup = (turn_tx + 0.5, belt_ty+1) = turn belt
    #    drop   = (turn_tx - 1.5, belt_ty+1) = rightmost drill
    # -----------------------------------------------------------------------
    feed_x = turn_tx - 1   # template → game turn_tx-0.5
    actions.append(_place("inserter", float(feed_x), float(belt_ty + 1), EAST))

    # -----------------------------------------------------------------------
    # 5. Chain inserters: right drill → left drill (facing EAST)
    #    At midpoint between adjacent drills, at belt_ty+1 (drill level)
    # -----------------------------------------------------------------------
    for i in range(len(drill_xs) - 1):
        right_x = drill_xs[i + 1]
        left_x  = drill_xs[i]
        chain_x = math.floor((right_x + left_x) / 2)
        actions.append(_place("inserter", float(chain_x), float(belt_ty + 1), EAST))

    # -----------------------------------------------------------------------
    # 6. Seed coal into all drills
    # -----------------------------------------------------------------------
    for ddx in drill_xs:
        actions.append(_insert(float(ddx), float(dy), "coal", seed_coal))

    output = IronMiningOutput(
        belt_x   = float(dx - 1) + 0.5,   # game x of leftmost belt tile
        belt_y   = float(belt_ty) + 0.5,   # game y
        belt_dir = WEST,
    )
    return actions, output


# ---------------------------------------------------------------------------
# Patch size utilities
# ---------------------------------------------------------------------------

def max_drills_on_patch(
    client,
    resource_name: str,
    drill_x: float,
    drill_y: float,
    direction: str = "EAST",   # direction drills are placed (EAST = increasing x)
    max_check: int = 20,
) -> int:
    """
    Return how many drills fit on the patch starting at (drill_x, drill_y).
    Checks each candidate position for: placeable + has target resource in footprint.
    """
    from game_integration.factorio_bridge import execute_lua as _exe

    positions = []
    dx = int(round(drill_x))
    dy = int(round(drill_y))
    for i in range(max_check):
        if direction == "EAST":
            cx = dx + i * 3
        else:
            cx = dx - i * 3
        positions.append((cx, dy))

    lua = """
local surface = game.surfaces['nauvis']
local count = 0
local positions = {""" + ", ".join(f"{{{x},{y}}}" for x, y in positions) + """}
for _, pos in ipairs(positions) do
    -- Check if footprint has only target resource (patch purity check)
    local target_tiles = surface.find_entities_filtered{
        name=""" + f'"{resource_name}"' + """,
        area={{pos[1]-1, pos[2]-1}, {pos[1]+1, pos[2]+1}}
    }
    local all_tiles = surface.find_entities_filtered{
        type="resource",
        area={{pos[1]-1, pos[2]-1}, {pos[1]+1, pos[2]+1}}
    }
    local pure = #target_tiles == #all_tiles and #target_tiles >= 2

    if pure then
        -- Position is on the right patch — count it regardless of blocking
        -- (blocking may be from our own belts/inserters placed earlier)
        count = count + 1
    else
        -- Off patch or mixed — stop here
        break
    end
end
rcon.print(count)
"""
    result = _exe(client, lua)
    if result["status"] == "OK":
        try:
            return int(result["output"].strip())
        except ValueError:
            pass
    return 1


if __name__ == "__main__":
    print("=== COAL MINING BLOCK ===")
    actions, output = place_coal_mining(drill_x=77, drill_y=17, num_drills=2)
    print(f"Generated {len(actions)} actions, output at game ({output.belt_x+0.5},{output.belt_y})")

    print()
    print("=== IRON MINING BLOCK ===")
    actions, output = place_iron_mining(drill_x=53, drill_y=19, num_drills=2)
    print(f"Generated {len(actions)} actions")
    for i, a in enumerate(actions, 1):
        if a["type"] == "PLACE_ENTITY":
            x, y = a["x"], a["y"]
            print(f"  {i:2d}. PLACE {a['entity_name']:25s} "
                  f"@ template({x},{y}) → game({x+0.5},{y+0.5}) {a['direction']}")
        else:
            print(f"  {i:2d}. FUEL  ({a['entity_x']},{a['entity_y']}) "
                  f"← {a['item_name']} x{a['count']}")
    print(f"Output belt at game ({output.belt_x},{output.belt_y}) dir={DIR_NAMES[output.belt_dir]}")

if __name__ == "__main__":
    # Match reference: drills at (77,17) and (80,17)
    # turn_tx = 77-2 = 75, collector_ty=18, output_ty=15
    # But reference shows output at y=16.5 → template y=16? Let me verify:
    # Reference: output belt at y=16.5, drill at y=17
    # dy=17, output_ty = dy-2 = 15 → game 15.5... but reference shows 16.5
    # 
    # Hmm: dy-2=15, game=15.5 vs reference y=16.5 → off by 1
    # Try dy-1: game=16.5 ✓

    actions, output = place_coal_mining(
        drill_x=77,
        drill_y=17,
        num_drills=2,
        seed_coal=10,
    )

    print(f"Generated {len(actions)} actions")
    print(f"Output belt at game ({output.belt_x+0.5}, {output.belt_y}) "
          f"dir={DIR_NAMES[output.belt_dir]}")
    print()
    for i, a in enumerate(actions, 1):
        if a["type"] == "PLACE_ENTITY":
            x, y = a["x"], a["y"]
            print(f"  {i:2d}. PLACE {a['entity_name']:25s} "
                  f"@ template({x},{y}) → game({x+0.5},{y+0.5}) {a['direction']}")
        else:
            print(f"  {i:2d}. FUEL  ({a['entity_x']},{a['entity_y']}) "
                  f"← {a['item_name']} x{a['count']}")


def find_patch_edge(
    client,
    resource_name: str,
    near_x: float,
    near_y: float,
    edge: str = "west",
    search_radius: float = 40,
) -> tuple[float, float] | None:
    """
    Find the westernmost (or northernmost/easternmost/southernmost) valid
    drill position on a pure resource patch near the hint position.

    This gives the starting anchor for place_coal_mining / place_iron_mining
    so drills extend across the full patch rather than from the center.

    Args:
        edge: "west" | "east" | "north" | "south" — which edge to find
    """
    from game_integration.factorio_bridge import execute_lua as _exe

    # Direction to sort candidates
    if edge == "west":
        sort_key = "a.position.x < b.position.x"
        ore_filter = f"math.abs(ore.position.y - near_y) <= 10"
    elif edge == "east":
        sort_key = "a.position.x > b.position.x"
        ore_filter = f"math.abs(ore.position.y - near_y) <= 10"
    elif edge == "north":
        sort_key = "a.position.y < b.position.y"
        ore_filter = f"math.abs(ore.position.x - near_x) <= 10"
    else:  # south
        sort_key = "a.position.y > b.position.y"
        ore_filter = f"math.abs(ore.position.x - near_x) <= 10"

    lua = f"""
local surface = game.surfaces['nauvis']
local near_x = {near_x}
local near_y = {near_y}
local all_ores = surface.find_entities_filtered{{
    name="{resource_name}",
    area={{
        {{near_x - {search_radius}, near_y - {search_radius}}},
        {{near_x + {search_radius}, near_y + {search_radius}}}
    }}
}}

-- Filter to tiles near the hint Y (for west/east) or hint X (for north/south)
local ores = {{}}
for _, ore in ipairs(all_ores) do
    if {ore_filter} then
        table.insert(ores, ore)
    end
end

-- Sort by edge direction
table.sort(ores, function(a, b) return {sort_key} end)

-- Walk from edge inward, find first pure placeable 2x2
for _, ore in ipairs(ores) do
    local cx = math.floor(ore.position.x + 0.5)
    local cy = math.floor(ore.position.y + 0.5)

    local can = surface.can_place_entity{{
        name="burner-mining-drill", position={{cx, cy}}, force="player"
    }}
    if can then
        local target = surface.find_entities_filtered{{
            name="{resource_name}",
            area={{{{cx-1,cy-1}},{{cx+1,cy+1}}}}
        }}
        local all_res = surface.find_entities_filtered{{
            type="resource",
            area={{{{cx-1,cy-1}},{{cx+1,cy+1}}}}
        }}
        -- Pure patch: all resource tiles are target type, at least 2
        if #target == #all_res and #target >= 2 then
            rcon.print(cx..","..cy)
            return
        end
    end
end
rcon.print("NONE")
"""

    result = _exe(client, lua)
    if result["status"] != "OK" or result["output"].strip() == "NONE":
        return None
    parts = result["output"].strip().split(",")
    return float(parts[0]), float(parts[1])


@dataclass
class SmeltingOutput:
    """Describes where iron plates exit from a smelting block."""
    belt_x: float      # game x of leftmost output belt tile (chest end)
    belt_y: float      # game y of output belt
    chest_x: float     # game x of chest
    chest_y: float     # game y of chest


def place_smelting(
    furnace_x: float,
    furnace_y: float,
    num_furnaces: int,
    chest_type: str = "wooden-chest",
    seed_coal: int = 5,
) -> tuple[list[dict], SmeltingOutput]:
    """
    Place a smelting block connected to the dual-lane main belt.

    The main belt (ore left, coal right) runs at game y = furnace_y + 2.5.
    This means furnace_y = drill_y - 4 when used with place_iron_mining.

    Reference geometry (furnace_y=-7):
      main belt:      game y=-4.5 (template -5)
      input inserter: game(x+0.5,-5.5) S: pickup=-4.5(main belt), drop=-6.5(furnace)
      furnace:        game y=-7   (template -7)
      output inserter:game(x+0.5,-8.5) S: pickup=-7.5(furnace), drop=-9.5(output belt)
      output belt:    game y=-9.5 (template -10)
      chest + final inserter at west end

    Args:
        furnace_x:    x of LEFTMOST furnace (= iron drill leftmost x)
        furnace_y:    y of furnaces (= drill_y - 4)
        num_furnaces: number of furnaces
        chest_type:   "wooden-chest" | "iron-chest" | "steel-chest"
        seed_coal:    initial coal for furnaces
    """
    actions = []

    fx = int(_snap2(furnace_x))
    fy = int(_snap2(furnace_y))

    furnace_xs = [fx + i * 3 for i in range(num_furnaces)]

    # Template y positions (verified from reference):
    in_ins_ty      = fy + 1   # game fy+1.5: picks main belt(fy+2.5), drops furnace(fy+0.5)
    out_ins_ty     = fy - 2   # game fy-1.5: picks furnace(fy-0.5), drops output belt(fy-2.5)
    output_belt_ty = fy - 3   # game fy-2.5

    right_tx = max(furnace_xs)      # rightmost furnace x
    chest_tx = fx - 3              # 3 tiles left of leftmost furnace
    fi_tx    = fx - 2              # 2 tiles left

    # 1. Furnaces
    for ffx in furnace_xs:
        actions.append(_place("stone-furnace", float(ffx), float(fy), NORTH))

    # 2. Output belt WEST — stop before final inserter position
    for x in range(right_tx, fi_tx, -1):
        actions.append(_place("transport-belt", float(x), float(output_belt_ty), WEST))

    # 3. Input inserters: main belt → furnace
    for ffx in furnace_xs:
        actions.append(_place("inserter", float(ffx), float(in_ins_ty), SOUTH))

    # 4. Output inserters: furnace → output belt
    for ffx in furnace_xs:
        actions.append(_place("inserter", float(ffx), float(out_ins_ty), SOUTH))

    # 5. Chest + final inserter
    actions.append(_place(chest_type, float(chest_tx), float(output_belt_ty), NORTH))
    actions.append(_place("inserter", float(fi_tx), float(output_belt_ty), EAST))

    # 6. Seed coal
    for ffx in furnace_xs:
        actions.append(_insert(float(ffx), float(fy), "coal", seed_coal))

    output = SmeltingOutput(
        belt_x  = float(chest_tx) + 0.5,
        belt_y  = float(output_belt_ty) + 0.5,
        chest_x = float(chest_tx) + 0.5,
        chest_y = float(output_belt_ty) + 0.5,
    )
    return actions, output


if __name__ == "__main__":
    print("=== SMELTING BLOCK ===")
    # Reference: furnaces at (53,-7),(56,-7),(59,-7), ore belt at y=-8.5 (game)
    actions, out = place_smelting(
        furnace_x=53, furnace_y=-7,
        num_furnaces=3,
        ore_belt_y=-8.5,
    )
    print(f"Generated {len(actions)} actions")
    for i, a in enumerate(actions, 1):
        if a["type"] == "PLACE_ENTITY":
            x, y = a["x"], a["y"]
            print(f"  {i:2d}. PLACE {a['entity_name']:25s} "
                  f"@ template({x},{y}) → game({x+0.5},{y+0.5}) {a['direction']}")
        else:
            print(f"  {i:2d}. FUEL  ({a['entity_x']},{a['entity_y']}) "
                  f"← {a['item_name']} x{a['count']}")
    print(f"Output belt game y={out.belt_y}, chest game ({out.chest_x},{out.chest_y})")


# ---------------------------------------------------------------------------
# Belt connection
# ---------------------------------------------------------------------------

def connect_coal_to_main_belt(
    coal_output_x: float,
    coal_output_y: float,
    main_belt_x: float,
    main_belt_y: float,
) -> list[dict]:
    """
    Route a belt from coal mining output to join the main iron ore belt right lane.

    Coal output belt flows WEST. We need to:
    1. Run EAST from coal output end to connect to the main belt east end
    2. The main belt right lane then carries coal west alongside iron ore

    Args:
        coal_output_x: game x of coal output belt leftmost tile (west end)
        coal_output_y: game y of coal output belt
        main_belt_x:   game x of main belt east end (rightmost tile)
        main_belt_y:   game y of main belt
    """
    import math
    actions = []

    fx = math.floor(coal_output_x)
    fy = math.floor(coal_output_y)
    tx = math.floor(main_belt_x)
    ty = math.floor(main_belt_y)

    # Route: vertical at fx from fy toward ty, then horizontal at ty toward tx
    # The vertical segment ends one tile SOUTH of ty so items flow onto main belt
    if fy != ty:
        vdir = NORTH if ty < fy else SOUTH
        y = fy
        # Stop one tile before ty — items flow naturally onto main belt
        stop_y = ty + (1 if ty < fy else -1)
        while y != stop_y:
            actions.append(_place("transport-belt", float(fx), float(y), vdir))
            y += -1 if ty < fy else 1

    # Horizontal segment: from fx to tx+1 (one tile east of main belt end)
    # so coal enters right lane from the east
    if fx != tx:
        hdir = EAST if tx > fx else WEST
        target_x = tx + (1 if hdir == EAST else -1)
        x = fx
        while x != target_x:
            actions.append(_place("transport-belt", float(x), float(ty), hdir))
            x += 1 if target_x > fx else -1

    return actions


# ---------------------------------------------------------------------------
# Water / steam power
# ---------------------------------------------------------------------------

def find_water_position(client, near_x: float = 0, near_y: float = 0,
                        search_radius: float = 200) -> tuple[float, float, str] | None:
    """
    Find a water tile for WEST-facing offshore pump.
    Pump at (x,y) facing WEST: output at (x+1,y), boiler at (x+3,y-0.5).
    Searches for position where pump, pipe, and boiler can all be placed.
    Returns (pump_x, pump_y, "WEST") or None.
    """
    from game_integration.factorio_bridge import execute_lua as _exe

    lua = f"""
local surface = game.surfaces['nauvis']
local near_x = {near_x}
local near_y = {near_y}
local search_r = {int(search_radius)}
local best_x, best_y, best_dist = 0, 0, math.huge

for dx = -search_r, search_r, 2 do
    for dy = -search_r, search_r, 2 do
        local x = near_x + dx
        local y = near_y + dy
        local tile = surface.get_tile(x, y)
        if tile and (tile.name == "water" or tile.name == "deepwater") then
            -- Pump faces WEST: output goes east (+x)
            local can_pump = surface.can_place_entity{{
                name="offshore-pump", position={{x,y}},
                force="player", direction=defines.direction.west
            }}
            -- Check pipe at x+1, boiler at x+3 y-0.5, engine at x+7 y-2
            local can_pipe = surface.can_place_entity{{
                name="pipe", position={{x+1, y}}, force="player"
            }}
            local can_boiler = surface.can_place_entity{{
                name="boiler", position={{x+3, y}}, force="player"
            }}
            local can_engine = surface.can_place_entity{{
                name="steam-engine", position={{x+7, y-2}}, force="player"
            }}
            if can_pump and can_pipe and can_boiler and can_engine then
                local dist = dx*dx + dy*dy
                if dist < best_dist then
                    best_dist = dist
                    best_x = x
                    best_y = y
                end
            end
        end
    end
end

if best_dist < math.huge then
    rcon.print(best_x..","..best_y..",WEST")
else
    rcon.print("NONE")
end
"""
    result = _exe(client, lua)
    if result["status"] != "OK" or result["output"].strip() == "NONE":
        return None
    parts = result["output"].strip().split(",")
    return float(parts[0]), float(parts[1]), parts[2]

@dataclass
class SteamNetworkOutput:
    """Describes the power output of a steam network."""
    engine_x: float
    engine_y: float
    pole_x: float
    pole_y: float


def place_steam_network(
    water_x: float,
    water_y: float,
    pump_direction: str = "WEST",
) -> tuple[list[dict], SteamNetworkOutput]:
    """
    Place offshore pump + pipe + boiler + pipes + steam engine.

    Uses verified working layout for WEST-facing pump:
      pump(0,0)W → pipe(1,0) → boiler(3,-0.5)N → pipe(3,-2) →
      pipe(4,-2) → engine(7,-2)E → pole(11,-3)

    All offsets relative to pump position (water_x, water_y).
    """
    actions = []
    wx = int(round(water_x))
    wy = int(round(water_y))

    # Verified working offsets (dx, dy) relative to pump, from reference factory
    layout = [
        ("offshore-pump",        0,     0,    WEST),
        ("pipe",                 1,     0,    NORTH),
        ("boiler",               3,    -0.5,  NORTH),
        ("pipe",                 3,    -2,    NORTH),
        ("pipe",                 4,    -2,    NORTH),
        ("steam-engine",         7,    -2,    EAST),
        ("small-electric-pole",  11,   -3,    NORTH),
    ]

    ex, ey, px, py = 0.0, 0.0, 0.0, 0.0
    for name, ox, oy, d in layout:
        ax = float(wx + ox)
        ay = float(wy + oy)
        actions.append(_place(name, ax, ay, d))
        if name == "boiler":
            actions.append(_insert(ax, ay, "coal", 50))
        elif name == "steam-engine":
            ex, ey = ax, ay
        elif name == "small-electric-pole":
            px, py = ax, ay

    output = SteamNetworkOutput(
        engine_x = ex + 0.5,
        engine_y = ey + 0.5,
        pole_x   = px + 0.5,
        pole_y   = py + 0.5,
    )
    return actions, output



# ---------------------------------------------------------------------------
# Power line
# ---------------------------------------------------------------------------

def place_power_line(
    from_x: float,
    from_y: float,
    to_x: float,
    to_y: float,
    pole_spacing: int = 7,
) -> list[dict]:
    """
    Place a sequence of small electric poles from (from_x,from_y) to (to_x,to_y).

    Poles are spaced pole_spacing tiles apart (max wire reach of small pole = 9 tiles).
    Routes in L-shape: horizontal first, then vertical.

    Args:
        from_x, from_y: starting position (near power source)
        to_x, to_y:     ending position (near factory)
        pole_spacing:   tiles between poles (default 7, safe for 9-tile wire reach)
    """
    import math
    actions = []

    fx, fy = round(from_x), round(from_y)
    tx, ty = round(to_x),   round(to_y)

    positions = []

    # Horizontal segment
    if fx != tx:
        step = pole_spacing if tx > fx else -pole_spacing
        x = fx
        while (step > 0 and x <= tx) or (step < 0 and x >= tx):
            positions.append((x, fy))
            x += step
        # Ensure we reach tx
        if positions[-1][0] != tx:
            positions.append((tx, fy))

    # Vertical segment
    if fy != ty:
        step = pole_spacing if ty > fy else -pole_spacing
        corner_x = tx
        y = fy + (pole_spacing if ty > fy else -pole_spacing)
        while (step > 0 and y <= ty) or (step < 0 and y >= ty):
            positions.append((corner_x, y))
            y += step
        if not positions or positions[-1] != (tx, ty):
            positions.append((tx, ty))

    # Deduplicate
    seen = set()
    for px, py in positions:
        if (px, py) not in seen:
            seen.add((px, py))
            actions.append(_place("small-electric-pole", float(px), float(py), NORTH))

    return actions