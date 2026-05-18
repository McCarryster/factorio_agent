"""
inspect_map.py

Full map inspector. Shows:
- All player-built entities grouped by type
- Spatial grid layout (ASCII map)
- Belt runs with lane contents
- Entity connections (inserter pickup/drop targets)
- Power network summary

Usage:
    python3 inspect_map.py
"""

import sys
import json
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).parent.parent))

from game_integration.dependencies import get_factorio_client
from game_integration.factorio_bridge import execute_lua


# ---------------------------------------------------------------------------
# Lua query — everything in one call
# ---------------------------------------------------------------------------

_LUA_FULL_MAP = """
local surface = game.surfaces['nauvis']
local force   = game.forces['player']
local results = {}

local entities = surface.find_entities_filtered{force=force}

for _, e in ipairs(entities) do
    if e.type == "character" then goto continue end

    local entry = {
        name      = e.name,
        type      = e.type,
        x         = e.position.x,
        y         = e.position.y,
        direction = e.direction or 0,
        status    = e.status or -1,
        electric_network_id = e.electric_network_id or -1,
    }

    -- Fuel inventory
    local fuel_inv = nil
    if e.type == "mining-drill" or e.type == "furnace" or e.type == "boiler" then
        fuel_inv = e.get_inventory(defines.inventory.fuel)
    end
    if fuel_inv then
        local fuel = {}
        for _, item in ipairs(fuel_inv.get_contents()) do
            local c = item.count
            if type(c) == "table" then c = c.count or 0 end
            if c > 0 then fuel[item.name] = c end
        end
        entry.fuel = fuel
    else
        entry.fuel = {}
    end

    -- Input/output for furnaces
    entry.input  = {}
    entry.output = {}
    if e.type == "furnace" then
        local src = e.get_inventory(defines.inventory.furnace_source)
        local res = e.get_inventory(defines.inventory.furnace_result)
        if src then
            for _, item in ipairs(src.get_contents()) do
                local c = item.count
                if type(c) == "table" then c = c.count or 0 end
                if c > 0 then entry.input[item.name] = c end
            end
        end
        if res then
            for _, item in ipairs(res.get_contents()) do
                local c = item.count
                if type(c) == "table" then c = c.count or 0 end
                if c > 0 then entry.output[item.name] = c end
            end
        end
    end

    -- Chest contents
    entry.chest = {}
    if e.type == "container" then
        local inv = e.get_inventory(defines.inventory.chest)
        if inv then
            for _, item in ipairs(inv.get_contents()) do
                local c = item.count
                if type(c) == "table" then c = c.count or 0 end
                if c > 0 then entry.chest[item.name] = c end
            end
        end
    end

    -- Belt lane contents
    entry.belt_left  = {}
    entry.belt_right = {}
    if e.type == "transport-belt" then
        local max_lines = e.get_max_transport_line_index()
        for line_idx = 1, max_lines do
            local line = e.get_transport_line(line_idx)
            if line then
                local items = {}
                for _, item in ipairs(line.get_contents()) do
                    local c = item.count
                    if type(c) == "table" then c = c.count or 0 end
                    if c > 0 then items[item.name] = c end
                end
                if line_idx == 1 then entry.belt_left  = items
                else                   entry.belt_right = items end
            end
        end
    end

    -- Inserter pickup/drop positions (actual API values, not computed offsets)
    entry.pickup_x = -1
    entry.pickup_y = -1
    entry.drop_x   = -1
    entry.drop_y   = -1
    if e.type == "inserter" then
        entry.pickup_x = e.pickup_position.x
        entry.pickup_y = e.pickup_position.y
        entry.drop_x   = e.drop_position.x
        entry.drop_y   = e.drop_position.y
    end

    results[#results + 1] = entry
    ::continue::
end

rcon.print(helpers.table_to_json(results))
"""

# ---------------------------------------------------------------------------
# Status map
# ---------------------------------------------------------------------------

STATUS_MAP = {
    1:"WORKING", 2:"NORMAL", 3:"GHOST", 4:"BROKEN",
    5:"NOT_PLUGGED_IN", 6:"NETS_CONNECTED", 7:"NETS_DISCONNECTED",
    14:"LOW_POWER", 18:"NO_INGREDIENTS", 19:"NO_RECIPE",
    21:"NO_MINABLE_RESOURCES", 27:"FULL_OUTPUT", 34:"WAITING_FOR_SPACE",
    53:"NO_FUEL", 54:"NO_POWER",
}
DIR = {0:"N", 4:"E", 8:"S", 12:"W"}

def status_str(raw) -> str:
    if raw == -1: return "—"
    return STATUS_MAP.get(raw, f"#{raw}")

def dir_str(d) -> str:
    return DIR.get(d, str(d))

# ---------------------------------------------------------------------------
# Grouping helpers
# ---------------------------------------------------------------------------

INFRA_TYPES = {"transport-belt", "underground-belt", "splitter",
               "pipe", "pipe-to-ground", "electric-pole"}

def group_entities(entities: list[dict]) -> dict[str, list]:
    groups = defaultdict(list)
    for e in entities:
        groups[e["type"]].append(e)
    return groups

# ---------------------------------------------------------------------------
# ASCII spatial map
# ---------------------------------------------------------------------------

def build_ascii_map(entities: list[dict]) -> str:
    if not entities:
        return "(empty)"

    # Find bounding box
    xs = [e["x"] for e in entities]
    ys = [e["y"] for e in entities]
    min_x, max_x = int(min(xs)) - 1, int(max(xs)) + 2
    min_y, max_y = int(min(ys)) - 1, int(max(ys)) + 2

    # Limit size to avoid huge maps
    if (max_x - min_x) > 80 or (max_y - min_y) > 40:
        return f"(map too large to display: {max_x-min_x}×{max_y-min_y} tiles)"

    # Build grid
    ABBREV = {
        "burner-mining-drill":  "Dr",
        "stone-furnace":        "Fu",
        "electric-furnace":     "EF",
        "steel-furnace":        "SF",
        "inserter":             "In",
        "fast-inserter":        "FI",
        "transport-belt":       "==",
        "underground-belt":     "UB",
        "splitter":             "SP",
        "small-electric-pole":  "Po",
        "medium-electric-pole": "MP",
        "big-electric-pole":    "BP",
        "offshore-pump":        "OP",
        "boiler":               "Bo",
        "steam-engine":         "SE",
        "pipe":                 "..",
        "pipe-to-ground":       "PG",
        "wooden-chest":         "Ch",
        "iron-chest":           "IC",
        "steel-chest":          "SC",
        "assembling-machine-1": "A1",
        "assembling-machine-2": "A2",
        "lab":                  "La",
    }

    width  = max_x - min_x + 1
    height = max_y - min_y + 1
    grid   = [["  "] * width for _ in range(height)]

    for e in entities:
        gx = int(e["x"]) - min_x
        gy = int(e["y"]) - min_y
        if 0 <= gx < width and 0 <= gy < height:
            abbrev = ABBREV.get(e["name"], e["name"][:2].upper())
            grid[gy][gx] = abbrev

    lines = []
    # X axis header
    x_labels = "".join(
        f"{min_x + i:2d}" if (min_x + i) % 5 == 0 else "  "
        for i in range(width)
    )
    lines.append(f"     {x_labels}")
    lines.append(f"     {'--' * width}")

    for row_i, row in enumerate(grid):
        y_label = min_y + row_i
        row_str = "".join(row)
        lines.append(f"{y_label:4d} |{row_str}|")

    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Belt run analysis
# ---------------------------------------------------------------------------

def analyze_belt_runs(belts: list[dict]) -> list[dict]:
    if not belts:
        return []

    sorted_belts = sorted(belts, key=lambda b: (round(b["y"]*2), round(b["x"]*2)))
    runs = []
    current = [sorted_belts[0]]

    for belt in sorted_belts[1:]:
        prev = current[-1]
        dist = ((belt["x"]-prev["x"])**2 + (belt["y"]-prev["y"])**2)**0.5
        if belt["direction"] == prev["direction"] and dist <= 1.5:
            current.append(belt)
        else:
            runs.append(current)
            current = [belt]
    runs.append(current)

    result = []
    for run in runs:
        left_totals:  dict[str,int] = {}
        right_totals: dict[str,int] = {}
        for b in run:
            for k,v in b["belt_left"].items():
                left_totals[k] = left_totals.get(k,0) + v
            for k,v in b["belt_right"].items():
                right_totals[k] = right_totals.get(k,0) + v

        has_left  = bool(left_totals)
        has_right = bool(right_totals)
        if has_left and has_right:
            if set(left_totals) == set(right_totals):
                lane_type = "DUAL_SAME"
            else:
                lane_type = "DUAL_MIXED"
        elif has_left or has_right:
            lane_type = "SINGLE"
        else:
            lane_type = "EMPTY"

        result.append({
            "count":       len(run),
            "direction":   DIR.get(run[0]["direction"], "?"),
            "from":        (run[0]["x"], run[0]["y"]),
            "to":          (run[-1]["x"], run[-1]["y"]),
            "lane_type":   lane_type,
            "left_items":  left_totals,
            "right_items": right_totals,
        })
    return result

# ---------------------------------------------------------------------------
# Inserter connection display
# ---------------------------------------------------------------------------

def find_entity_at(entities, tx, ty, radius=1.5):
    best, best_d = None, radius
    for e in entities:
        d = ((e["x"]-tx)**2 + (e["y"]-ty)**2)**0.5
        if d < best_d:
            best_d = d
            best = e
    return best

def describe_inserters(inserters: list[dict], all_entities: list[dict]) -> list[str]:
    lines = []
    for e in inserters:
        px, py   = e.get("pickup_x", -1), e.get("pickup_y", -1)
        dpx, dpy = e.get("drop_x",   -1), e.get("drop_y",   -1)
        src = find_entity_at(all_entities, px, py)   if px != -1 else None
        dst = find_entity_at(all_entities, dpx, dpy) if dpx != -1 else None
        src_str = f"{src['name']}@({src['x']},{src['y']})" if src else f"empty@({px:.1f},{py:.1f})"
        dst_str = f"{dst['name']}@({dst['x']},{dst['y']})" if dst else f"empty@({dpx:.1f},{dpy:.1f})"
        status  = status_str(e["status"])
        lines.append(
            f"  inserter@({e['x']},{e['y']}) facing {dir_str(e['direction'])} [{status}]"
            f"\n    picks from: {src_str}"
            f"\n    drops to:   {dst_str}"
        )
    return lines

# ---------------------------------------------------------------------------
# Main inspector
# ---------------------------------------------------------------------------

def inspect_map(client) -> None:
    print("Querying game state...")
    result = execute_lua(client, _LUA_FULL_MAP)

    if result["status"] == "ERROR":
        print(f"Lua error: {result['output']}")
        return

    if not result["output"].strip():
        print("No player entities on map.")
        return

    try:
        entities = json.loads(result["output"])
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        return

    if not entities:
        print("No player entities found.")
        return

    groups = group_entities(entities)

    # ---- OVERVIEW ----
    print(f"\n{'='*60}")
    print(f"MAP OVERVIEW — {len(entities)} entities")
    print(f"{'='*60}")

    type_counts = {t: len(es) for t, es in sorted(groups.items())}
    for t, count in type_counts.items():
        print(f"  {t:30s} × {count}")

    # ---- ASCII MAP ----
    print(f"\n{'='*60}")
    print("SPATIAL LAYOUT (ASCII)")
    print(f"{'='*60}")
    print(build_ascii_map(entities))

    # ---- PRODUCTION ENTITIES ----
    production_types = ["mining-drill", "furnace", "stone-furnace",
                        "electric-furnace", "steel-furnace", "assembling-machine-1",
                        "assembling-machine-2", "boiler", "steam-engine", "offshore-pump"]

    prod_entities = [e for e in entities
                     if any(t in e["name"] for t in production_types)]

    if prod_entities:
        print(f"\n{'='*60}")
        print("PRODUCTION ENTITIES")
        print(f"{'='*60}")
        for e in sorted(prod_entities, key=lambda x: (x["y"], x["x"])):
            st = status_str(e["status"])
            d  = dir_str(e["direction"])
            line = f"  {e['name']:30s} @({e['x']:6.1f},{e['y']:6.1f}) dir={d} [{st}]"
            if e.get("fuel"):
                line += f"  fuel:{e['fuel']}"
            if e.get("input"):
                line += f"  in:{e['input']}"
            if e.get("output"):
                line += f"  out:{e['output']}"
            print(line)

    # ---- INSERTERS ----
    inserters = groups.get("inserter", []) + groups.get("fast-inserter", [])
    if inserters:
        print(f"\n{'='*60}")
        print(f"INSERTERS ({len(inserters)})")
        print(f"{'='*60}")
        for line in describe_inserters(inserters, entities):
            print(line)

    # ---- BELT RUNS ----
    belts = groups.get("transport-belt", [])
    if belts:
        print(f"\n{'='*60}")
        print(f"BELT RUNS ({len(belts)} belts)")
        print(f"{'='*60}")
        runs = analyze_belt_runs(belts)
        for i, run in enumerate(runs, 1):
            left  = ", ".join(f"{k}:{v}" for k,v in run["left_items"].items()) or "empty"
            right = ", ".join(f"{k}:{v}" for k,v in run["right_items"].items()) or "empty"
            print(f"  Run #{i}: {run['count']} belt(s) facing {run['direction']} "
                  f"from {run['from']} to {run['to']}")
            print(f"    Type:  {run['lane_type']}")
            print(f"    Left:  {left}")
            print(f"    Right: {right}")

    # ---- CHESTS ----
    chests = groups.get("container", [])
    if chests:
        print(f"\n{'='*60}")
        print(f"CHESTS ({len(chests)})")
        print(f"{'='*60}")
        for e in chests:
            contents = ", ".join(f"{k}:{v}" for k,v in e.get("chest", {}).items()) or "empty"
            print(f"  {e['name']}@({e['x']},{e['y']}): {contents}")

    # ---- POWER ----
    poles = [e for e in entities if "electric-pole" in e["name"]]
    if poles:
        print(f"\n{'='*60}")
        print(f"POWER NETWORK ({len(poles)} poles)")
        print(f"{'='*60}")
        networks: dict[int, list] = defaultdict(list)
        for p in poles:
            nid = p["electric_network_id"]
            networks[nid].append(p)
        for nid, ps in sorted(networks.items()):
            print(f"  Network #{nid}: {len(ps)} pole(s)")
            xs = [p["x"] for p in ps]
            ys = [p["y"] for p in ps]
            print(f"    x range: {min(xs):.1f} – {max(xs):.1f}")
            print(f"    y range: {min(ys):.1f} – {max(ys):.1f}")


if __name__ == "__main__":
    # print("Connecting to Factorio...")
    client = get_factorio_client()


    inspect_map(client)



    # lua = """
    # local surface = game.surfaces['nauvis']
    # local drill = surface.find_entity("burner-mining-drill", {93, -2})
    # if drill then
    #     rcon.print("drop=("..drill.drop_position.x..","..drill.drop_position.y..")")
    # end
    # -- Check what's at the connector start
    # local belt = surface.find_entity("transport-belt", {94.5, -2.5})
    # if belt then
    #     rcon.print("belt at (94.5,-2.5) dir="..belt.direction)
    # else
    #     rcon.print("NO BELT at (94.5,-2.5)")
    # end
    # -- Check nearby
    # local ents = surface.find_entities_filtered{
    #     area={{92,-4},{97,0}}, force="player"
    # }
    # for _, e in ipairs(ents) do
    #     if e.type ~= "character" then
    #         rcon.print(e.name.."@("..e.position.x..","..e.position.y..") dir="..e.direction)
    #     end
    # end
    # """
    # result = execute_lua(client, lua)
    # print(result["output"])