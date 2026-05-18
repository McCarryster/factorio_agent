"""
inspect_belts.py

Inspects all transport belts on the map and shows:
- What items are on each belt (left lane / right lane)
- Whether a belt run carries single or dual items
- Connected belt sequences grouped by flow direction

Usage:
    python3 inspect_belts.py
"""

import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from game_integration.dependencies import get_factorio_client
from game_integration.factorio_bridge import execute_lua


_LUA_BELT_CONTENTS = """
local surface = game.surfaces['nauvis']
local force = game.forces['player']
local results = {}

local belts = surface.find_entities_filtered{
    type = "transport-belt",
    force = force
}

for _, belt in ipairs(belts) do
    local left_items  = {}
    local right_items = {}

    -- Each belt has 2 transport lines (0=left, 1=right in Factorio 2.x)
    local line_count = belt.get_max_transport_line_index()
    for line_idx = 1, line_count do
        local line = belt.get_transport_line(line_idx)
        local line_items = {}
        if line then
            local contents = line.get_contents()
            for _, item in ipairs(contents) do
                -- In Factorio 2.x item.count may be a table {count=N, quality=...}
                local c = item.count
                if type(c) == "table" then c = c.count or 0 end
                if type(c) ~= "number" then c = 0 end
                if c > 0 then
                    table.insert(line_items, {name=item.name, count=c})
                end
            end
        end
        if line_idx == 1 then
            left_items = line_items
        else
            right_items = line_items
        end
    end

    table.insert(results, {
        x         = belt.position.x,
        y         = belt.position.y,
        direction = belt.direction,
        left      = left_items,
        right     = right_items,
    })
end

rcon.print(helpers.table_to_json(results))
"""

DIRECTION_NAMES = {0: "NORTH", 4: "EAST", 8: "SOUTH", 12: "WEST"}

def _items_str(items: list) -> str:
    if not items:
        return "empty"
    return ", ".join(f"{i['name']}:{i['count']}" for i in items)


def _belt_key(belt: dict) -> str:
    return f"({belt['x']},{belt['y']})"


def inspect_belts(client) -> None:
    result = execute_lua(client, _LUA_BELT_CONTENTS)

    if result["status"] == "ERROR":
        print(f"Lua error: {result['output']}")
        return

    if not result["output"].strip():
        print("No transport belts found on map.")
        return

    try:
        belts = json.loads(result["output"])
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        print(f"Raw: {result['output'][:200]}")
        return

    if not belts:
        print("No transport belts found.")
        return

    print(f"Found {len(belts)} transport belt(s)\n")

    # --- Group into runs (consecutive same-direction belts) ---
    # Sort by position for readable output
    belts.sort(key=lambda b: (round(b['y']), round(b['x'])))

    # Classify each belt
    def classify(belt):
        has_left  = bool(belt['left'])
        has_right = bool(belt['right'])
        if has_left and has_right:
            # Check if same or different items
            left_names  = {i['name'] for i in belt['left']}
            right_names = {i['name'] for i in belt['right']}
            if left_names == right_names:
                return "DUAL_SAME"
            else:
                return "DUAL_MIXED"
        elif has_left or has_right:
            return "SINGLE"
        else:
            return "EMPTY"

    # Group consecutive belts into runs
    runs = []
    current_run = []

    for belt in belts:
        if not current_run:
            current_run.append(belt)
            continue

        prev = current_run[-1]
        # Same direction and adjacent (within 1.5 tiles)
        dist = ((belt['x'] - prev['x'])**2 + (belt['y'] - prev['y'])**2)**0.5
        same_dir = belt['direction'] == prev['direction']

        if same_dir and dist <= 1.5:
            current_run.append(belt)
        else:
            runs.append(current_run)
            current_run = [belt]

    if current_run:
        runs.append(current_run)

    # Print each run
    for i, run in enumerate(runs, 1):
        first = run[0]
        last  = run[-1]
        direction = DIRECTION_NAMES.get(first['direction'], str(first['direction']))
        length = len(run)

        # Collect all item types across the run
        all_left_items:  dict[str, int] = {}
        all_right_items: dict[str, int] = {}
        for belt in run:
            for item in belt['left']:
                all_left_items[item['name']] = all_left_items.get(item['name'], 0) + item['count']
            for item in belt['right']:
                all_right_items[item['name']] = all_right_items.get(item['name'], 0) + item['count']

        # Classify the run
        has_left  = bool(all_left_items)
        has_right = bool(all_right_items)

        if has_left and has_right:
            left_names  = set(all_left_items.keys())
            right_names = set(all_right_items.keys())
            if left_names == right_names:
                run_type = "DUAL (same item on both lanes)"
            else:
                run_type = "DUAL MIXED (different items on each lane)"
        elif has_left or has_right:
            run_type = "SINGLE LANE"
        else:
            run_type = "EMPTY"

        print(f"Belt run #{i} — {length} belt(s) facing {direction}")
        print(f"  From: ({first['x']},{first['y']})  To: ({last['x']},{last['y']})")
        print(f"  Type: {run_type}")
        if all_left_items:
            items_str = ", ".join(f"{k}:{v}" for k, v in all_left_items.items())
            print(f"  Left lane:  {items_str}")
        else:
            print(f"  Left lane:  empty")
        if all_right_items:
            items_str = ", ".join(f"{k}:{v}" for k, v in all_right_items.items())
            print(f"  Right lane: {items_str}")
        else:
            print(f"  Right lane: empty")
        print()

    # Summary
    print("=" * 50)
    print("SUMMARY")
    print("=" * 50)
    total_empty = sum(1 for b in belts if not b['left'] and not b['right'])
    total_single = sum(1 for b in belts
                       if (bool(b['left']) != bool(b['right'])))
    total_dual_same = sum(1 for b in belts
                          if b['left'] and b['right']
                          and {i['name'] for i in b['left']} == {i['name'] for i in b['right']})
    total_dual_mixed = sum(1 for b in belts
                           if b['left'] and b['right']
                           and {i['name'] for i in b['left']} != {i['name'] for i in b['right']})

    print(f"Total belts:      {len(belts)}")
    print(f"Empty:            {total_empty}")
    print(f"Single lane:      {total_single}")
    print(f"Dual same item:   {total_dual_same}")
    print(f"Dual mixed items: {total_dual_mixed}")


if __name__ == "__main__":
    print("Connecting to Factorio...\n")
    client = get_factorio_client()

    ping = execute_lua(client, "rcon.print('pong')")
    assert ping["output"].strip() == "pong", f"Connection failed: {ping}"
    print("Connected.\n")

    inspect_belts(client)