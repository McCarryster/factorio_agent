"""
observation.py

Observe raw game state each agent iteration.
Returns structured data the LLM can reason about directly.
"""


def get_inventory(client) -> dict[str, int]:
    """Return player inventory as {item_name: count}."""
    from game_integration.factorio_bridge import execute_lua
    lua = """
local inv = game.players[1].get_main_inventory()
local contents = inv.get_contents()
local parts = {}
for _, item in ipairs(contents) do
    table.insert(parts, item.name .. "=" .. item.count)
end
rcon.print(table.concat(parts, "|"))
"""
    out = execute_lua(client, lua).get("output", "").strip()
    if not out:
        return {}
    result = {}
    for part in out.split("|"):
        if "=" in part:
            name, count = part.rsplit("=", 1)
            result[name] = int(count)
    return result


def get_research_status(client) -> dict[str, bool]:
    """Return which technologies are researched."""
    from game_integration.factorio_bridge import execute_lua
    lua = """
local force = game.forces.player
local techs = {"steam-power", "electronics", "automation", "logistics"}
local parts = {}
for _, name in ipairs(techs) do
    local tech = force.technologies[name]
    if tech then
        table.insert(parts, name .. "=" .. tostring(tech.researched))
    end
end
rcon.print(table.concat(parts, "|"))
"""
    out = execute_lua(client, lua).get("output", "").strip()
    result = {}
    for part in out.split("|"):
        if "=" in part:
            name, val = part.split("=", 1)
            result[name] = val == "true"
    return result


def get_recipe_statuses(client, names: list[str]) -> dict[str, bool]:
    """Check which recipes are currently enabled for player force."""
    from game_integration.factorio_bridge import execute_lua
    name_list = "{" + ",".join(f'"{n}"' for n in names) + "}"
    lua = f"""
local force = game.forces.player
local names = {name_list}
local parts = {{}}
for _, name in ipairs(names) do
    local r = force.recipes[name]
    table.insert(parts, name .. "=" .. tostring(r and r.enabled or false))
end
rcon.print(table.concat(parts, "|"))
"""
    out = execute_lua(client, lua).get("output", "").strip()
    result = {}
    for part in out.split("|"):
        if "=" in part:
            name, val = part.split("=", 1)
            result[name] = val == "true"
    return result


def get_entities(client) -> list[dict]:
    """
    Return all player-built entities with position, status, and inventory contents.
    Shows fuel, input items, and output items for each entity.
    """
    from game_integration.factorio_bridge import execute_lua
    lua = """
local surface = game.players[1].surface
local ents = surface.find_entities_filtered{force="player"}
local parts = {}
for _, e in ipairs(ents) do
    if e.type ~= "character" then
        -- Status
        local status_map = {
            [defines.entity_status.working] = "WORKING",
            [defines.entity_status.normal] = "WORKING",
            [defines.entity_status.full_output] = "FULL_OUTPUT",
            [defines.entity_status.no_fuel] = "NO_FUEL",
            [defines.entity_status.no_ingredients] = "NO_INGREDIENTS",
            [defines.entity_status.waiting_for_space_in_destination] = "WAITING",
        }
        local status = (e.status and status_map[e.status]) or "UNKNOWN"

        -- Fuel
        local fuel_str = ""
        local fuel_inv = e.get_inventory(defines.inventory.fuel)
        if fuel_inv then
            local c = fuel_inv.get_contents()
            local parts2 = {}
            for _, item in ipairs(c) do
                table.insert(parts2, item.name.."="..item.count)
            end
            if #parts2 > 0 then fuel_str = table.concat(parts2, ";") end
        end

        -- Input inventory (furnace source, assembler input)
        local input_str = ""
        for _, inv_id in ipairs({defines.inventory.furnace_source,
                                  defines.inventory.assembling_machine_input}) do
            local inv = e.get_inventory(inv_id)
            if inv then
                local c = inv.get_contents()
                local parts2 = {}
                for _, item in ipairs(c) do
                    table.insert(parts2, item.name.."="..item.count)
                end
                if #parts2 > 0 then input_str = table.concat(parts2, ";"); break end
            end
        end

        -- Output inventory
        local output_str = ""
        for _, inv_id in ipairs({defines.inventory.furnace_result,
                                  defines.inventory.assembling_machine_output,
                                  defines.inventory.chest,
                                  defines.inventory.item_main}) do
            local inv = e.get_inventory(inv_id)
            if inv then
                local c = inv.get_contents()
                local parts2 = {}
                for _, item in ipairs(c) do
                    table.insert(parts2, item.name.."="..item.count)
                end
                if #parts2 > 0 then output_str = table.concat(parts2, ";"); break end
            end
        end

        local dir_names = {"north","_","_","_","east","_","_","_","south","_","_","_","west"}
        local dir = dir_names[(e.direction or 0) + 1] or "north"

        table.insert(parts, e.name.."|"..
            string.format("%.1f", e.position.x).."|"..
            string.format("%.1f", e.position.y).."|"..
            dir.."|"..status.."|fuel:"..fuel_str..
            "|in:"..input_str.."|out:"..output_str)
    end
end
if #parts == 0 then rcon.print("NONE")
else rcon.print(table.concat(parts, "||")) end
"""
    out = execute_lua(client, lua).get("output", "").strip()
    if not out or out == "NONE":
        return []

    entities = []
    for part in out.split("||"):
        bits = part.split("|")
        if len(bits) >= 8:
            entities.append({
                "name":    bits[0],
                "x":       float(bits[1]),
                "y":       float(bits[2]),
                "direction": bits[3],
                "status":  bits[4],
                "fuel":    bits[5].replace("fuel:", ""),
                "input":   bits[6].replace("in:", ""),
                "output":  bits[7].replace("out:", ""),
            })
    return entities


def get_power_networks(client) -> list[dict]:
    """Return power network summary."""
    from game_integration.factorio_bridge import execute_lua
    lua = """
local surface = game.players[1].surface
local poles = surface.find_entities_filtered{type="electric-pole", force="player"}
local networks = {}
local seen = {}
for _, pole in ipairs(poles) do
    local net = pole.electric_network_id
    if net and not seen[net] then
        seen[net] = true
        local stats = pole.electric_network_statistics
        table.insert(networks, "net"..net..":poles="..#poles)
    end
end
if #networks == 0 then rcon.print("NONE")
else rcon.print(table.concat(networks, "|")) end
"""
    out = execute_lua(client, lua).get("output", "").strip()
    if out == "NONE" or not out:
        return []
    networks = []
    for part in out.split("|"):
        networks.append({"raw": part})
    return networks


def format_state(client, last_result: dict | None = None) -> str:
    """
    Build full observation string using existing build_observation infrastructure.
    Adds smelting progress section on top.
    """
    from game_integration.factorio_bridge import execute_lua
    from metrics.entity_status import get_entity_status, format_entity_status
    from metrics.production_tracker import ProductionTracker
    from agent.observability.build_observation import get_player_inventory
    import json
    from pathlib import Path

    inventory = get_player_inventory(client)
    entities  = get_entity_status(client)

    sections = []

    # Entity status
    sections.append(format_entity_status(entities))

    # Inventory
    inv_str = ", ".join(f"{k}: {v}" for k, v in inventory.items()) or "empty"
    sections.append(f"=== INVENTORY ===\n{inv_str}")

    # Researched technologies
    techs_result = execute_lua(client, """
local techs = {}
for name, tech in pairs(game.players[1].force.technologies) do
    if tech.researched then techs[#techs+1] = name end
end
rcon.print(helpers.table_to_json(techs))
""")
    researched = []
    if techs_result.get("status") == "OK" and techs_result.get("output"):
        try:
            researched = json.loads(techs_result["output"])
        except Exception:
            pass
    tech_str = ", ".join(researched) if researched else "none"
    sections.append(f"=== RESEARCHED TECHNOLOGIES ===\n{tech_str}")

    # Smelting progress (critical for bootstrap milestones)
    iron_in_inv   = inventory.get("iron-plate", 0)
    copper_in_inv = inventory.get("copper-plate", 0)

    # Also count plates sitting in furnace output inventories
    iron_in_furnaces   = 0
    copper_in_furnaces = 0
    for e in (entities if isinstance(entities, list) else []):
        out = getattr(e, "output", {}) or {}
        iron_in_furnaces   += out.get("iron-plate", 0)
        copper_in_furnaces += out.get("copper-plate", 0)

    sections.append(
        f"=== SMELTING PROGRESS ===\n"
        f"iron-plate:   {iron_in_inv} in inventory + {iron_in_furnaces} in furnaces "
        f"= {iron_in_inv + iron_in_furnaces} / 50 needed (for steam-power unlock)\n"
        f"copper-plate: {copper_in_inv} in inventory + {copper_in_furnaces} in furnaces "
        f"= {copper_in_inv + copper_in_furnaces} / 10 needed (for electronics unlock)\n"
        f"NOTE: call take_item to collect plates from furnaces into inventory!"
    )

    # Recipe unlock status for key items
    recipe_check = execute_lua(client, """
local names = {"pipe","boiler","steam-engine","offshore-pump",
               "inserter","small-electric-pole","copper-cable","electronic-circuit"}
local parts = {}
for _, n in ipairs(names) do
    local r = game.forces.player.recipes[n]
    table.insert(parts, n.."="..(r and tostring(r.enabled) or "false"))
end
rcon.print(table.concat(parts, "|"))
""")
    if recipe_check.get("status") == "OK" and recipe_check.get("output"):
        locked, unlocked = [], []
        for part in recipe_check["output"].strip().split("|"):
            if "=" in part:
                name, val = part.split("=", 1)
                (unlocked if val == "true" else locked).append(name)
        sections.append(
            f"=== KEY RECIPES ===\n"
            f"unlocked: {', '.join(unlocked) or 'none'}\n"
            f"locked:   {', '.join(locked) or 'none'}"
        )

    # Last action result
    if last_result:
        status = "SUCCEEDED" if last_result.get("status") == "OK" else "FAILED"
        out = last_result.get("output") or "(no output)"
        sections.append(f"=== LAST ACTION ===\n{status}\n{out}")

    return "\n\n".join(sections)