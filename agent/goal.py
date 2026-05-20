"""
Check whether the iron plate factory goal is met.

Goal: Fully automated iron plate production with:
  - ≥2 coal mining drills (burner, WORKING)
  - ≥5 iron ore mining drills (burner, WORKING)  
  - ≥5 stone furnaces smelting iron ore (WORKING)
  - chest receiving iron plates automatically
  - everything connected with belts, inserters, power
  - no manual intervention needed
"""

from __future__ import annotations


GOAL_DESCRIPTION = """
Build a fully automated iron plate factory that runs without manual intervention:
- At least 2 coal mining drills mining coal (WORKING)
- At least 5 iron ore mining drills mining iron ore (WORKING)  
- At least 5 stone furnaces smelting iron ore into iron plates (WORKING)
- A chest collecting the iron plates
- All connected via belts, inserters, and electric power
- Steam engine providing electricity
"""


def check_goal(client) -> tuple[bool, str]:
    """
    Check if the factory goal is met.
    Returns (success, reason_string).
    """
    from game_integration.factorio_bridge import execute_lua

    lua = """
local surface = game.players[1].surface
local force = game.players[1].force

-- Count entities by type and status
local coal_drills = 0
local iron_drills = 0
local furnaces_working = 0
local has_chest = false
local has_power = false

-- Check status helpers
local function is_working(e)
    return e.status == defines.entity_status.working
        or e.status == defines.entity_status.full_output
end

local drills = surface.find_entities_filtered{
    name="burner-mining-drill", force=force
}
for _, d in ipairs(drills) do
    if is_working(d) then
        -- Check what it's mining
        local resources = surface.find_entities_filtered{
            type="resource",
            area={{d.position.x-1, d.position.y-1},
                  {d.position.x+1, d.position.y+1}}
        }
        if #resources > 0 then
            if resources[1].name == "coal" then
                coal_drills = coal_drills + 1
            elseif resources[1].name == "iron-ore" then
                iron_drills = iron_drills + 1
            end
        end
    end
end

local furnaces = surface.find_entities_filtered{
    name="stone-furnace", force=force
}
for _, f in ipairs(furnaces) do
    if is_working(f) then
        furnaces_working = furnaces_working + 1
    end
end

-- Check for chest with iron plates
local chests = surface.find_entities_filtered{
    type="container", force=force
}
for _, c in ipairs(chests) do
    local inv = c.get_inventory(defines.inventory.chest)
    if inv and inv.get_item_count("iron-plate") > 0 then
        has_chest = true
    end
end

-- Check for working steam engine (power)
local engines = surface.find_entities_filtered{
    name="steam-engine", force=force
}
for _, e in ipairs(engines) do
    if is_working(e) then has_power = true end
end

rcon.print("coal="..coal_drills..
           "|iron="..iron_drills..
           "|furnaces="..furnaces_working..
           "|chest="..tostring(has_chest)..
           "|power="..tostring(has_power))
"""
    out = execute_lua(client, lua).get("output", "").strip()

    data = {}
    for part in out.split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            data[k] = v

    coal     = int(data.get("coal", 0))
    iron     = int(data.get("iron", 0))
    furnaces = int(data.get("furnaces", 0))
    chest    = data.get("chest", "false") == "true"
    power    = data.get("power", "false") == "true"

    reasons = []
    if coal < 2:
        reasons.append(f"need ≥2 working coal drills (have {coal})")
    if iron < 5:
        reasons.append(f"need ≥5 working iron drills (have {iron})")
    if furnaces < 5:
        reasons.append(f"need ≥5 working furnaces (have {furnaces})")
    if not chest:
        reasons.append("no chest with iron plates")
    if not power:
        reasons.append("no working steam engine")

    if reasons:
        return False, "Goal not met: " + "; ".join(reasons)
    return True, "Goal met! Automated iron plate factory is running."


def format_goal_status(client) -> str:
    """Return human-readable goal status for the LLM prompt."""
    done, reason = check_goal(client)
    if done:
        return f"✓ GOAL COMPLETE: {reason}"
    return f"✗ GOAL NOT MET: {reason}"