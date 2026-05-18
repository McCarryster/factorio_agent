"""
factory_template.py

Generates PLACE_ENTITY + INSERT_ITEM primitive actions to build
a complete iron plate factory adapted to any map.

Key design:
  - Iron drills placed ON iron ore patch, facing NORTH toward main belt
  - Coal drills placed ON coal patch, output routed to main belt right lane
  - Main belt: dual lane (iron-ore left, coal right)
  - Furnaces north of main belt, fed by input inserters
  - Output belt north of furnaces, carries iron plates west to chest

Geometry verified from reference factory:
  drill_y          = iron ore patch y (integer, 2x2)
  belt_y           = drill_y - 1.5   (main belt, 0.5 grid)
  in_ins_y         = drill_y - 2.5   (input inserter)
  furnace_y        = drill_y - 4.0   (furnace center, integer)
  out_ins_y        = drill_y - 5.5   (output inserter)
  output_y         = drill_y - 6.5   (output belt)
"""

from __future__ import annotations
import math

NORTH = 0
EAST  = 4
SOUTH = 8
WEST  = 12
DIR_NAMES = {NORTH:"NORTH", EAST:"EAST", SOUTH:"SOUTH", WEST:"WEST"}
OPPOSITE  = {NORTH:SOUTH, SOUTH:NORTH, EAST:WEST, WEST:EAST}


def _snap1(v: float) -> float:
    """Snap 1x1 entity to 0.5 grid."""
    return round(v * 2) / 2


def _snap2(v: float) -> float:
    """Snap 2x2 entity center to integer (Factorio rounds half-up)."""
    return float(int(v + 0.5) if v >= 0 else -int(-v + 0.5))


def _primary_direction(ax, ay, bx, by) -> int:
    dx, dy = bx - ax, by - ay
    if abs(dx) >= abs(dy):
        return EAST if dx >= 0 else WEST
    return SOUTH if dy >= 0 else NORTH


def _belt_path(
    from_x: float, from_y: float,
    to_x: float, to_y: float,
) -> list[tuple[float, float, int]]:
    """
    Generate belt tiles for an L-shaped path from (from_x,from_y) to (to_x,to_y).
    Uses integer steps — game snaps each tile to X.5 center automatically.
    Goes horizontal first, then vertical.
    Returns list of (x, y, direction).
    """
    tiles = []
    # Use floor for stepping — game adds 0.5 when placing 1x1 entities
    # floor(-2.5) = -3 → game places at -2.5 ✓  (round(-2.5) = -2 → game places at -1.5 ✗)
    import math
    fx = math.floor(from_x)
    fy = math.floor(from_y)
    tx = math.floor(to_x)
    ty = math.floor(to_y)

    # Horizontal segment first (WEST or EAST)
    if fx != tx:
        hdir = WEST if tx < fx else EAST
        x = fx
        while x != tx:
            tiles.append((float(x), float(fy), hdir))
            x += -1 if tx < fx else 1

    # Vertical segment (NORTH or SOUTH)
    if fy != ty:
        vdir = NORTH if ty < fy else SOUTH
        y = fy
        while y != ty:
            tiles.append((float(tx), float(y), vdir))
            y += -1 if ty < fy else 1

    return tiles


def generate_factory_actions(
    iron_ore_center: tuple[float, float],
    coal_center: tuple[float, float],
    num_furnaces: int = 3,
    num_coal_drills: int = 2,
    seed_coal: int = 10,
    iron_drill_position: tuple[float, float] | None = None,
    coal_drill_position: tuple[float, float] | None = None,
) -> list[dict]:
    """
    Generate all primitive actions to build a complete iron plate factory.

    Args:
        iron_ore_center:    (x,y) center hint for iron ore patch
        coal_center:        (x,y) center hint for coal patch
        num_furnaces:       number of furnace units (default 3)
        num_coal_drills:    number of coal drills (default 2)
        seed_coal:          initial coal for coal drills
        iron_drill_position: override for first iron drill position (from find_drill_position)
        coal_drill_position: override for first coal drill position (from find_drill_position)
    """
    ix, iy = iron_drill_position if iron_drill_position else iron_ore_center
    cx, cy = coal_drill_position if coal_drill_position else coal_center

    # -----------------------------------------------------------------------
    # Iron array geometry (verified from reference factory)
    # -----------------------------------------------------------------------
    drill_y   = _snap2(iy)
    belt_y    = drill_y - 1.5
    in_ins_y  = drill_y - 2.5
    furnace_y = _snap2(drill_y - 4)
    out_ins_y = drill_y - 5.5
    output_y  = drill_y - 6.5

    unit_step = 3
    half      = (num_furnaces - 1) * unit_step / 2
    start_x   = round(ix - half)
    array_xs  = [start_x + i * unit_step for i in range(num_furnaces)]

    east_x = max(array_xs) + unit_step
    west_x = min(array_xs) - unit_step

    # Belt runs WEST (chest is on west side)
    belt_dir  = WEST
    belt_xs   = list(range(int(east_x), int(west_x) - 1, -1))
    chest_x   = west_x - 2   # chest 2 tiles west of belt end
    fi_x      = west_x - 1   # final inserter between belt and chest

    actions = []

    def place(entity, x, y, direction):
        actions.append({
            "type":        "PLACE_ENTITY",
            "entity_name": entity,
            "x":           float(x),
            "y":           float(y),
            "direction":   DIR_NAMES[direction],
        })

    def insert(x, y, item, count):
        actions.append({
            "type":      "INSERT_ITEM",
            "entity_x":  float(x),
            "entity_y":  float(y),
            "item_name": item,
            "count":     int(count),
        })

    # -----------------------------------------------------------------------
    # 1. Main belt (dual lane: iron-ore left, coal right)
    # -----------------------------------------------------------------------
    for x in belt_xs:
        place("transport-belt", x, belt_y, belt_dir)

    # -----------------------------------------------------------------------
    # 2. Output belt
    # -----------------------------------------------------------------------
    for x in belt_xs:
        place("transport-belt", x, output_y, belt_dir)

    # -----------------------------------------------------------------------
    # 3. Iron drills on ore patch, facing NORTH
    # -----------------------------------------------------------------------
    for ux in array_xs:
        place("burner-mining-drill", _snap2(ux), _snap2(drill_y), NORTH)

    # -----------------------------------------------------------------------
    # 4. Furnaces
    # -----------------------------------------------------------------------
    for ux in array_xs:
        place("stone-furnace", _snap2(ux), _snap2(furnace_y), NORTH)

    # -----------------------------------------------------------------------
    # 5. Input inserters: main belt → furnace (facing SOUTH)
    # -----------------------------------------------------------------------
    for ux in array_xs:
        place("inserter", _snap1(ux - 0.5), _snap1(in_ins_y), SOUTH)

    # -----------------------------------------------------------------------
    # 6. Output inserters: furnace → output belt (facing SOUTH)
    # -----------------------------------------------------------------------
    for ux in array_xs:
        place("inserter", _snap1(ux - 0.5), _snap1(out_ins_y), SOUTH)

    # -----------------------------------------------------------------------
    # 7. Iron drill fuel chain (coal passes between drills WEST)
    # -----------------------------------------------------------------------
    for i in range(len(array_xs) - 1):
        ax = array_xs[i + 1]   # eastern (coal-side) drill
        bx = array_xs[i]       # western drill
        chain_x = _snap1((ax + bx) / 2)
        chain_y = _snap1(drill_y + 0.5)
        place("inserter", chain_x, chain_y, WEST)

    # -----------------------------------------------------------------------
    # 8. Coal drills on coal patch
    #    Face toward main belt to output coal onto connector belt
    # -----------------------------------------------------------------------
    # Determine which direction from coal patch to iron array
    coal_to_iron_dir = _primary_direction(cx, cy, ix, iy)

    # Coal drill faces AWAY from iron (toward open ground) to output onto connector
    coal_drill_face = OPPOSITE[coal_to_iron_dir]

    # Place coal drills on coal patch, spaced 3 tiles apart
    coal_drill_y = _snap2(cy)
    coal_drill_xs = [_snap2(cx - (num_coal_drills - 1) * 1.5 + i * 3)
                     for i in range(num_coal_drills)]

    for cdx in coal_drill_xs:
        place("burner-mining-drill", cdx, coal_drill_y, coal_drill_face)

    # Coal drill chain inserter
    if num_coal_drills > 1:
        for i in range(1, num_coal_drills):
            cx2 = _snap1((coal_drill_xs[i-1] + coal_drill_xs[i]) / 2)
            # Feed coal from outer to inner (toward connector)
            place("inserter", cx2, _snap1(coal_drill_y), EAST)

    # -----------------------------------------------------------------------
    # 9. Coal connector belt: from coal drill output to main belt right entry
    #    Coal drill facing SOUTH: drop at (cdx, coal_drill_y + ~1.3) → snap to y+1.5
    #    Route belt from (coal_drill_xs[0], coal_drill_y+1.5) to (east_x, belt_y)
    # -----------------------------------------------------------------------
    # Drop position offset depends on facing direction
    # Drop offsets verified from game API (pickup_position / drop_position)
    # EAST drill: actual drop ≈ (+1.3, -0.5) from center → use (+1.5, -0.5) on 0.5 grid
    # SOUTH drill: actual drop ≈ (0, +1.3) from center → use (0, +1.5)
    drop_offsets = {NORTH: (0, -1.5), SOUTH: (0, 1.5), EAST: (1.5, -0.5), WEST: (-1.5, -0.5)}
    ddx, ddy = drop_offsets.get(coal_drill_face, (0, 1.5))

    # First coal drill drop position (nearest to main belt)
    first_drill_x = coal_drill_xs[0]
    coal_drop_x = _snap1(first_drill_x + ddx)
    coal_drop_y = _snap1(coal_drill_y + ddy)

    # Route belt from coal drop to east end of main belt
    # Enter main belt at east_x on right lane (right lane = south side of belt)
    # Items enter from east side: route to (east_x, belt_y)
    connector_tiles = _belt_path(coal_drop_x, coal_drop_y, east_x, belt_y)

    for bx, by, bdir in connector_tiles:
        place("transport-belt", bx, by, bdir)

    # -----------------------------------------------------------------------
    # 10. Chest and final inserter
    #     Inserter faces EAST: picks from belt (east), drops to chest (west)
    # -----------------------------------------------------------------------
    place("wooden-chest", _snap1(chest_x), _snap1(output_y), NORTH)
    place("inserter", _snap1(fi_x), _snap1(output_y), EAST)

    # -----------------------------------------------------------------------
    # 11. Fuel
    # -----------------------------------------------------------------------
    for cdx in coal_drill_xs:
        insert(cdx, coal_drill_y, "coal", seed_coal)

    for ux in array_xs:
        insert(ux, drill_y, "coal", 5)

    for ux in array_xs:
        insert(ux, furnace_y, "coal", 5)

    return actions


if __name__ == "__main__":
    iron_ore = (64.5, 1.0)
    coal     = (52.5, 15.0)

    coal_d = _primary_direction(iron_ore[0], iron_ore[1], coal[0], coal[1])
    iron_d = _primary_direction(coal[0], coal[1], iron_ore[0], iron_ore[1])
    print(f"Iron ore: {iron_ore}")
    print(f"Coal:     {coal}")
    print(f"Coal is {DIR_NAMES[coal_d]} of iron ore")
    print(f"Coal drill faces: {DIR_NAMES[OPPOSITE[iron_d]]} (away from iron)")
    print()

    actions = generate_factory_actions(iron_ore, coal, num_furnaces=3, num_coal_drills=2)
    print(f"=== {len(actions)} ACTIONS ===")
    for i, a in enumerate(actions, 1):
        if a["type"] == "PLACE_ENTITY":
            print(f"  {i:2d}. PLACE {a['entity_name']:25s} "
                  f"@ ({a['x']},{a['y']}) {a['direction']}")
        else:
            print(f"  {i:2d}. FUEL  ({a['entity_x']},{a['entity_y']}) "
                  f"<- {a['item_name']} x{a['count']}")