"""
skill_runner.py

Skill router and base class.

Usage:
    from agent.skill_runner import run_skill
    from agent.planner import PlannerAction

    result = run_skill(client, action)
    if result.ok:
        print(result.message)
    else:
        print(f"Failed: {result.message}")
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import importlib

from game_integration.factorio_bridge import execute_lua
import factorio_rcon


# ---------------------------------------------------------------------------
# Skill result
# ---------------------------------------------------------------------------

@dataclass
class SkillResult:
    ok: bool
    message: str
    details: dict = field(default_factory=dict)

    # Populated by router after execution
    action: str = ""
    target: str = ""

    @property
    def failed(self) -> bool:
        return not self.ok

    def __str__(self) -> str:
        status = "OK" if self.ok else "FAILED"
        return f"[{status}] {self.message}"


# ---------------------------------------------------------------------------
# Skill base class
# ---------------------------------------------------------------------------

class Skill:
    """
    Base class for all skills.

    Subclasses implement run() which receives the PlannerAction parameters
    and the RCON client, and returns a SkillResult.
    """

    # Override in subclass
    action_type: str = ""

    def run(
        self,
        client: factorio_rcon.RCONClient,
        target: str,
        parameters: dict,
    ) -> SkillResult:
        raise NotImplementedError

    def lua(self, path: str, **kwargs) -> str:
        """
        Load a Lua skill file from skills/ and format it with kwargs.
        Path is relative to the skills/ directory.
        """
        skills_dir = Path(__file__).parent.parent / "skills"
        lua_path = skills_dir / path
        if not lua_path.exists():
            raise FileNotFoundError(f"Lua skill not found: {lua_path}")
        return lua_path.read_text().format(**kwargs)

    def execute(
        self,
        client: factorio_rcon.RCONClient,
        lua_code: str,
    ) -> dict:
        """Run Lua and return the raw result dict."""
        return execute_lua(client, lua_code)

    def execute_json(
        self,
        client: factorio_rcon.RCONClient,
        lua_code: str,
    ) -> tuple[bool, dict | str]:
        """
        Run Lua that prints JSON, parse the output.
        Returns (success, parsed_dict_or_error_string).
        """
        import json
        result = execute_lua(client, lua_code)
        if result["status"] == "ERROR":
            return False, result["output"]
        try:
            return True, json.loads(result["output"])
        except Exception:
            return True, {"raw": result["output"]}


# ---------------------------------------------------------------------------
# Skill registry
# ---------------------------------------------------------------------------

# Maps ACTION_TYPE string → Skill subclass
_REGISTRY: dict[str, type[Skill]] = {}


def register(cls: type[Skill]) -> type[Skill]:
    """Decorator to register a skill class."""
    _REGISTRY[cls.action_type] = cls
    return cls


# ---------------------------------------------------------------------------
# Individual skills
# ---------------------------------------------------------------------------

@register
class ExtendPowerNetworkSkill(Skill):
    action_type = "EXTEND_POWER_NETWORK"

    def run(self, client, target, parameters):
        result = execute_lua(client, _LUA_EXTEND_POWER_NETWORK)
        if result["status"] == "ERROR":
            return SkillResult(
                ok=False,
                message=f"Lua error: {result['output']}",
            )

        output = result["output"].strip()

        # Parse structured output lines
        placed = 0
        blocked = False
        already_covered = False
        lines = output.splitlines()

        for line in lines:
            if line.startswith("PLACED:"):
                placed += 1
            elif line.startswith("BLOCKED:"):
                blocked = True
            elif line.startswith("ALREADY_COVERED"):
                already_covered = True

        if already_covered:
            return SkillResult(
                ok=True,
                message="Power network already covers all consumers — no poles needed.",
                details={"placed": 0, "output": output},
            )

        if placed == 0 and blocked:
            return SkillResult(
                ok=False,
                message=f"Could not place any poles — all candidate tiles blocked.\n{output}",
                details={"placed": 0, "blocked": True, "output": output},
            )

        return SkillResult(
            ok=True,
            message=f"Placed {placed} pole(s).\n{output}",
            details={"placed": placed, "output": output},
        )


@register
class InsertFuelSkill(Skill):
    action_type = "INSERT_FUEL"

    def run(self, client, target, parameters):
        fuel_item = parameters.get("fuel_item", "coal")
        amount    = int(parameters.get("amount", 10))

        lua = _LUA_INSERT_FUEL.format(
            fuel_item=fuel_item,
            amount=amount,
        )
        result = execute_lua(client, lua)

        if result["status"] == "ERROR":
            return SkillResult(ok=False, message=f"Lua error: {result['output']}")

        output = result["output"].strip()
        fueled = sum(1 for l in output.splitlines() if l.startswith("FUELED:"))

        if fueled == 0:
            return SkillResult(
                ok=False,
                message=f"No entities fueled. {output}",
                details={"output": output},
            )

        return SkillResult(
            ok=True,
            message=f"Fueled {fueled} entity/entities with {fuel_item}.",
            details={"fueled": fueled, "output": output},
        )


@register
class WaitSkill(Skill):
    action_type = "WAIT"

    def run(self, client, target, parameters):
        import time
        seconds = int(parameters.get("seconds", 5))
        time.sleep(seconds)
        return SkillResult(
            ok=True,
            message=f"Waited {seconds} seconds.",
        )


@register
class InspectEntitySkill(Skill):
    action_type = "INSPECT_ENTITY"

    def run(self, client, target, parameters):
        result = execute_lua(client, _LUA_INSPECT)
        if result["status"] == "ERROR":
            return SkillResult(ok=False, message=f"Lua error: {result['output']}")
        return SkillResult(
            ok=True,
            message=result["output"].strip(),
            details={"output": result["output"]},
        )


# Stubs for actions not yet implemented
for _stub_action in ("PLACE_ENTITY", "REMOVE_ENTITY", "ROTATE_ENTITY",
                     "CONNECT_POWER", "MOVE_TO", "CRAFT_ITEM", "TRANSFER_ITEM"):
    def _make_stub(action_name):
        @register
        class _StubSkill(Skill):
            action_type = action_name
            def run(self, client, target, parameters):
                return SkillResult(
                    ok=False,
                    message=f"{action_name} skill not yet implemented.",
                )
        _StubSkill.__name__ = f"{action_name}Skill"
        return _StubSkill
    _make_stub(_stub_action)


# ---------------------------------------------------------------------------
# Router — main entry point
# ---------------------------------------------------------------------------

def run_skill(
    client: factorio_rcon.RCONClient,
    action,                          # PlannerAction
) -> SkillResult:
    """
    Dispatch a PlannerAction to the matching skill and run it.

    Args:
        client:  RCON client
        action:  PlannerAction from planner.choose_next_action()

    Returns:
        SkillResult with ok/failed + message
    """
    action_type = action.action
    skill_cls   = _REGISTRY.get(action_type)

    if skill_cls is None:
        return SkillResult(
            ok=False,
            message=f"No skill registered for action type '{action_type}'.",
            action=action_type,
            target=action.target,
        )

    skill  = skill_cls()
    result = skill.run(client, action.target, action.parameters)
    result.action = action_type
    result.target = action.target
    return result


# ---------------------------------------------------------------------------
# Lua skill code (inline for now, move to skills/*.lua once stable)
# ---------------------------------------------------------------------------

_LUA_EXTEND_POWER_NETWORK = """
local surface = game.surfaces['nauvis']
local force   = game.forces['player']
local player  = game.players[1]

-- Find all electric consumers that have NO_POWER or NOT_PLUGGED_IN
local consumer_types = {
    "inserter", "electric-mining-drill", "assembling-machine",
    "electric-furnace", "lab", "radar", "lamp"
}
local unpowered = {}
for _, etype in ipairs(consumer_types) do
    local ents = surface.find_entities_filtered{type=etype, force=force}
    for _, e in ipairs(ents) do
        local s = e.status
        if s == defines.entity_status.no_power
        or s == defines.entity_status.not_plugged_in_electric_network then
            table.insert(unpowered, e)
        end
    end
end

if #unpowered == 0 then
    rcon.print("ALREADY_COVERED: all consumers have power")
    return
end

-- Find all connected electric poles (those in a network with generators)
local poles = surface.find_entities_filtered{
    type="electric-pole", force=force
}
local connected_poles = {}
for _, p in ipairs(poles) do
    local network = p.electric_network
    if network and network.energy > 0 then
        table.insert(connected_poles, p)
    end
end

if #connected_poles == 0 then
    rcon.print("ERROR: no powered poles found — cannot extend network")
    return
end

-- Find the unpowered consumer farthest from any connected pole
-- (the "leading edge" of the gap)
local target_consumer = unpowered[1]
local max_min_dist = 0
for _, consumer in ipairs(unpowered) do
    local min_dist = math.huge
    for _, pole in ipairs(connected_poles) do
        local d = ((pole.position.x - consumer.position.x)^2
                 + (pole.position.y - consumer.position.y)^2)^0.5
        if d < min_dist then min_dist = d end
    end
    if min_dist > max_min_dist then
        max_min_dist = min_dist
        target_consumer = consumer
    end
end

-- Find the connected pole nearest to that consumer
local nearest_pole = connected_poles[1]
local nearest_dist = math.huge
for _, pole in ipairs(connected_poles) do
    local d = ((pole.position.x - target_consumer.position.x)^2
             + (pole.position.y - target_consumer.position.y)^2)^0.5
    if d < nearest_dist then
        nearest_dist = d
        nearest_pole = pole
    end
end

-- Walk from nearest_pole toward target_consumer placing poles every 7 tiles
local step     = 7.0
local max_poles = 5
local placed   = 0
local cx = nearest_pole.position.x
local cy = nearest_pole.position.y
local tx = target_consumer.position.x
local ty = target_consumer.position.y

for i = 1, max_poles do
    local dx   = tx - cx
    local dy   = ty - cy
    local dist = (dx*dx + dy*dy)^0.5

    if dist < 2 then
        rcon.print("REACHED_TARGET: consumer now within range")
        break
    end

    -- Normalise and step
    local nx = cx + (dx/dist) * step
    local ny = cy + (dy/dist) * step

    -- Snap to grid
    local sx = math.floor(nx) + 0.5
    local sy = math.floor(ny) + 0.5

    -- Check if player has poles
    local pole_count = player.get_item_count("small-electric-pole")
    if pole_count == 0 then
        rcon.print("NO_ITEMS: player has no small-electric-pole")
        break
    end

    -- Try to place (check collision first)
    local can_place = surface.can_place_entity{
        name     = "small-electric-pole",
        position = {sx, sy},
        force    = force,
    }

    if not can_place then
        -- Try nearby offsets
        local placed_ok = false
        for _, off in ipairs({{1,0},{-1,0},{0,1},{0,-1},{1,1},{-1,1},{1,-1},{-1,-1}}) do
            local ax = sx + off[1]
            local ay = sy + off[2]
            if surface.can_place_entity{name="small-electric-pole", position={ax,ay}, force=force} then
                surface.create_entity{
                    name     = "small-electric-pole",
                    position = {ax, ay},
                    force    = force,
                    player   = player,
                }
                player.remove_item{name="small-electric-pole", count=1}
                rcon.print("PLACED: small-electric-pole at (" .. ax .. "," .. ay .. ")")
                cx = ax
                cy = ay
                placed = placed + 1
                placed_ok = true
                break
            end
        end
        if not placed_ok then
            rcon.print("BLOCKED: could not place pole near (" .. sx .. "," .. sy .. ")")
        end
    else
        surface.create_entity{
            name     = "small-electric-pole",
            position = {sx, sy},
            force    = force,
            player   = player,
        }
        player.remove_item{name="small-electric-pole", count=1}
        rcon.print("PLACED: small-electric-pole at (" .. sx .. "," .. sy .. ")")
        cx = sx
        cy = sy
        placed = placed + 1
    end
end

if placed == 0 then
    rcon.print("BLOCKED: could not place any poles")
end
"""


_LUA_INSERT_FUEL = """
local surface = game.surfaces['nauvis']
local force   = game.forces['player']
local player  = game.players[1]
local fuel_item = "{fuel_item}"
local amount    = {amount}

local burner_types = {{"mining-drill", "furnace", "boiler"}}
local fueled = 0

for _, btype in ipairs(burner_types) do
    local ents = surface.find_entities_filtered{{type=btype, force=force}}
    for _, e in ipairs(ents) do
        local inv = e.get_inventory(defines.inventory.fuel)
        if inv then
            local current = inv.get_item_count(fuel_item)
            local capacity = inv.get_bar() or 5
            local needed = math.max(0, capacity - current)
            if needed > 0 and e.status == defines.entity_status.no_fuel then
                local to_insert = math.min(needed, amount,
                    player.get_item_count(fuel_item))
                if to_insert > 0 then
                    local inserted = e.insert{{name=fuel_item, count=to_insert}}
                    player.remove_item{{name=fuel_item, count=inserted}}
                    rcon.print("FUELED: " .. e.name ..
                        " at (" .. e.position.x .. "," .. e.position.y .. ")" ..
                        " with " .. inserted .. "x " .. fuel_item)
                    fueled = fueled + 1
                end
            end
        end
    end
end

if fueled == 0 then
    rcon.print("NONE_NEEDED: no entities required fuel")
end
"""


_LUA_INSPECT = """
local surface = game.surfaces['nauvis']
local force   = game.forces['player']
local results = {}

local types_to_check = {
    "mining-drill", "furnace", "assembling-machine",
    "inserter", "electric-pole", "boiler", "steam-engine"
}

for _, etype in ipairs(types_to_check) do
    local ents = surface.find_entities_filtered{type=etype, force=force}
    for _, e in ipairs(ents) do
        local status_val = e.status or -1
        table.insert(results,
            e.name .. "@(" .. e.position.x .. "," .. e.position.y .. ")" ..
            " status=" .. tostring(status_val)
        )
    end
end

rcon.print(table.concat(results, "\\n"))
"""