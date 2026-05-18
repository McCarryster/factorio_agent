"""
primitives.py

Defines the six primitive actions the executor LLM can emit.
Translates a list of primitive calls into a single Lua script
that executes them all and reports results.

Usage:
    from agent.primitives import translate_to_lua, validate_actions

    actions = [
        {"type": "PLACE_ENTITY", "entity_name": "small-electric-pole",
         "x": 41.5, "y": 27.5, "direction": "NORTH"},
        {"type": "INSERT_ITEM",  "entity_x": 43.0, "entity_y": 30.0,
         "item_name": "coal", "count": 10},
    ]

    errors = validate_actions(actions)
    if not errors:
        lua = translate_to_lua(actions)
        result = execute_lua(client, lua)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Direction mapping
# ---------------------------------------------------------------------------

DIRECTION_MAP = {
    "NORTH": 0,
    "EAST":  4,
    "SOUTH": 8,
    "WEST":  12,
}

# ---------------------------------------------------------------------------
# Primitive schemas
# ---------------------------------------------------------------------------
# Each entry: required fields and their expected types.

PRIMITIVE_SCHEMAS: dict[str, dict[str, type]] = {
    "PLACE_ENTITY": {
        "entity_name": str,
        "x":           float,
        "y":           float,
        "direction":   str,   # NORTH / EAST / SOUTH / WEST
    },
    "REMOVE_ENTITY": {
        "x": float,
        "y": float,
    },
    "ROTATE_ENTITY": {
        "x":         float,
        "y":         float,
        "direction": str,
    },
    "INSERT_ITEM": {
        "entity_x":  float,
        "entity_y":  float,
        "item_name": str,
        "count":     int,
    },
    "TAKE_ITEM": {
        "entity_x":  float,
        "entity_y":  float,
        "item_name": str,
        "count":     int,
    },
    "CRAFT_ITEM": {
        "item_name": str,
        "count":     int,
    },
}

ALLOWED_TYPES = set(PRIMITIVE_SCHEMAS.keys())


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@dataclass
class ValidationError:
    index: int
    action_type: str
    message: str

    def __str__(self):
        return f"Action[{self.index}] {self.action_type}: {self.message}"


def validate_actions(actions: list[dict]) -> list[ValidationError]:
    """
    Validate a list of primitive action dicts.
    Returns list of ValidationError — empty means all good.
    """
    errors: list[ValidationError] = []

    if not isinstance(actions, list):
        errors.append(ValidationError(0, "?", "actions must be a list"))
        return errors

    for i, action in enumerate(actions):
        if not isinstance(action, dict):
            errors.append(ValidationError(i, "?", "each action must be a dict"))
            continue

        action_type = action.get("type", "")

        if action_type not in ALLOWED_TYPES:
            errors.append(ValidationError(
                i, action_type,
                f"unknown action type '{action_type}'. "
                f"Allowed: {sorted(ALLOWED_TYPES)}"
            ))
            continue

        schema = PRIMITIVE_SCHEMAS[action_type]
        for field_name, expected_type in schema.items():
            if field_name not in action:
                errors.append(ValidationError(
                    i, action_type,
                    f"missing required field '{field_name}'"
                ))
                continue

            val = action[field_name]

            # Allow int where float is expected
            if expected_type == float and isinstance(val, int):
                continue

            if not isinstance(val, expected_type):
                errors.append(ValidationError(
                    i, action_type,
                    f"field '{field_name}' expected {expected_type.__name__}, "
                    f"got {type(val).__name__}"
                ))

            # Validate direction values
            if field_name == "direction" and val not in DIRECTION_MAP:
                errors.append(ValidationError(
                    i, action_type,
                    f"direction '{val}' not valid. Use: NORTH, EAST, SOUTH, WEST"
                ))

    return errors


# ---------------------------------------------------------------------------
# Lua translation
# ---------------------------------------------------------------------------

# Each primitive translates to a Lua snippet that:
#   - performs the action
#   - prints a structured result line: OK: ... or ERR: ...

_LUA_HEADER = """\
local surface = game.surfaces['nauvis']
local force   = game.forces['player']
local player  = game.players[1]
local results = {}
local actions_taken = 0
local max_actions   = {max_actions}

local function report(line)
    results[#results + 1] = line
end

local function snap(v)
    -- Snap to nearest 0.5 grid
    return math.floor(v * 2 + 0.5) / 2
end

local function find_entity_at(x, y)
    local ents = surface.find_entities_filtered{
        position = {x, y}, radius = 1.0
    }
    for _, e in ipairs(ents) do
        if e.force == force and e.type ~= "character" then
            return e
        end
    end
    return nil
end

"""

_LUA_FOOTER = """\

rcon.print(table.concat(results, "\\n"))
"""

def _lua_place_entity(action: dict, idx: int) -> str:
    direction = DIRECTION_MAP.get(action["direction"], 0)
    return f"""\
-- Action {idx}: PLACE_ENTITY
do
    if actions_taken >= max_actions then
        report("BUDGET_EXCEEDED: action {idx} PLACE_ENTITY skipped")
    else
        local name = "{action['entity_name']}"
        local pos  = {{snap({action['x']}), snap({action['y']})}}
        local dir  = {direction}
        if player.get_item_count(name) == 0 then
            report("ERR:{idx}:PLACE_ENTITY:no_item:" .. name .. " not in inventory")
        elseif not surface.can_place_entity{{name=name, position=pos, force=force, direction=dir}} then
            -- Try adjacent tiles before giving up
            local offsets = {{{{0,-1}},{{0,1}},{{-1,0}},{{1,0}},{{0,-2}},{{0,2}},{{-2,0}},{{2,0}}}}
            local placed_alt = false
            for _, off in ipairs(offsets) do
                local alt = {{pos[1]+off[1], pos[2]+off[2]}}
                if surface.can_place_entity{{name=name, position=alt, force=force, direction=dir}} then
                    local e2 = surface.create_entity{{name=name, position=alt, force=force, direction=dir, player=player, raise_built=true}}
                    if e2 then
                        player.remove_item{{name=name, count=1}}
                        if e2.type == "electric-pole" then
                            local nearby = surface.find_entities_filtered{{type="electric-pole", force=force, position=alt, radius=9.0}}
                            local my_conn = e2.get_wire_connector(defines.wire_connector_id.pole_copper, true)
                            for _, neighbour in ipairs(nearby) do
                                if neighbour ~= e2 then
                                    local their_conn = neighbour.get_wire_connector(defines.wire_connector_id.pole_copper, true)
                                    my_conn.connect_to(their_conn, true)
                                end
                            end
                        end
                        report("OK:{idx}:PLACE_ENTITY:" .. name .. ":(" .. alt[1] .. "," .. alt[2] .. ") [alt position]")
                        actions_taken = actions_taken + 1
                        placed_alt = true
                        break
                    end
                end
            end
            if not placed_alt then
                report("ERR:{idx}:PLACE_ENTITY:blocked:cannot place " .. name ..
                   " at (" .. pos[1] .. "," .. pos[2] .. ")")
            end
        else
            local e = surface.create_entity{{
                name=name, position=pos, force=force, direction=dir, player=player
            }}
            if e then
                player.remove_item{{name=name, count=1}}
                -- For electric poles: wire to all reachable poles (Factorio 2.x)
                if e.type == "electric-pole" then
                    local nearby = surface.find_entities_filtered{{
                        type="electric-pole", force=force,
                        position=pos, radius=9.0
                    }}
                    local my_conn = e.get_wire_connector(
                        defines.wire_connector_id.pole_copper, true)
                    for _, neighbour in ipairs(nearby) do
                        if neighbour ~= e then
                            local their_conn = neighbour.get_wire_connector(
                                defines.wire_connector_id.pole_copper, true)
                            my_conn.connect_to(their_conn, true)
                        end
                    end
                end
                report("OK:{idx}:PLACE_ENTITY:" .. name ..
                       ":(" .. pos[1] .. "," .. pos[2] .. ")")
                actions_taken = actions_taken + 1
            else
                report("ERR:{idx}:PLACE_ENTITY:failed:create_entity returned nil")
            end
        end
    end
end
"""

def _lua_remove_entity(action: dict, idx: int) -> str:
    return f"""\
-- Action {idx}: REMOVE_ENTITY
do
    if actions_taken >= max_actions then
        report("BUDGET_EXCEEDED: action {idx} REMOVE_ENTITY skipped")
    else
        local e = find_entity_at({action['x']}, {action['y']})
        if not e then
            report("ERR:{idx}:REMOVE_ENTITY:not_found:no entity at ({action['x']},{action['y']})")
        else
            local name = e.name
            local px   = e.position.x
            local py   = e.position.y
            e.mine({{force=true}})
            report("OK:{idx}:REMOVE_ENTITY:" .. name ..
                   ":(" .. px .. "," .. py .. ")")
            actions_taken = actions_taken + 1
        end
    end
end
"""

def _lua_rotate_entity(action: dict, idx: int) -> str:
    direction = DIRECTION_MAP.get(action["direction"], 0)
    return f"""\
-- Action {idx}: ROTATE_ENTITY
do
    if actions_taken >= max_actions then
        report("BUDGET_EXCEEDED: action {idx} ROTATE_ENTITY skipped")
    else
        local e = find_entity_at({action['x']}, {action['y']})
        if not e then
            report("ERR:{idx}:ROTATE_ENTITY:not_found:no entity at ({action['x']},{action['y']})")
        else
            e.direction = {direction}
            report("OK:{idx}:ROTATE_ENTITY:" .. e.name ..
                   ":(" .. e.position.x .. "," .. e.position.y .. ")" ..
                   ":dir={action['direction']}")
            actions_taken = actions_taken + 1
        end
    end
end
"""

def _lua_insert_item(action: dict, idx: int) -> str:
    return f"""\
-- Action {idx}: INSERT_ITEM
do
    local e = find_entity_at({action['entity_x']}, {action['entity_y']})
    if not e then
        report("ERR:{idx}:INSERT_ITEM:not_found:no entity at ({action['entity_x']},{action['entity_y']})")
    else
        local have = player.get_item_count("{action['item_name']}")
        local want = {action['count']}
        local give = math.min(have, want)
        if give == 0 then
            report("ERR:{idx}:INSERT_ITEM:no_item:{action['item_name']} not in inventory")
        else
            local inserted = e.insert{{name="{action['item_name']}", count=give}}
            player.remove_item{{name="{action['item_name']}", count=inserted}}
            report("OK:{idx}:INSERT_ITEM:{action['item_name']}:x" .. inserted ..
                   ":into:" .. e.name ..
                   ":(" .. e.position.x .. "," .. e.position.y .. ")")
        end
    end
end
"""

def _lua_take_item(action: dict, idx: int) -> str:
    return f"""\
-- Action {idx}: TAKE_ITEM
do
    local e = find_entity_at({action['entity_x']}, {action['entity_y']})
    if not e then
        report("ERR:{idx}:TAKE_ITEM:not_found:no entity at ({action['entity_x']},{action['entity_y']})")
    else
        local removed = e.remove_item{{name="{action['item_name']}", count={action['count']}}}
        if removed == 0 then
            report("ERR:{idx}:TAKE_ITEM:not_found:{action['item_name']} not in entity")
        else
            player.insert{{name="{action['item_name']}", count=removed}}
            report("OK:{idx}:TAKE_ITEM:{action['item_name']}:x" .. removed ..
                   ":from:" .. e.name ..
                   ":(" .. e.position.x .. "," .. e.position.y .. ")")
        end
    end
end
"""

def _lua_craft_item(action: dict, idx: int) -> str:
    return f"""\
-- Action {idx}: CRAFT_ITEM
do
    if actions_taken >= max_actions then
        report("BUDGET_EXCEEDED: action {idx} CRAFT_ITEM skipped")
    else
        local crafted = player.begin_crafting{{
            recipe="{action['item_name']}", count={action['count']}
        }}
        if crafted == 0 then
            report("ERR:{idx}:CRAFT_ITEM:failed:cannot craft {action['item_name']} x{action['count']}")
        else
            report("OK:{idx}:CRAFT_ITEM:{action['item_name']}:x" .. crafted)
            actions_taken = actions_taken + 1
        end
    end
end
"""

_TRANSLATORS = {
    "PLACE_ENTITY":  _lua_place_entity,
    "REMOVE_ENTITY": _lua_remove_entity,
    "ROTATE_ENTITY": _lua_rotate_entity,
    "INSERT_ITEM":   _lua_insert_item,
    "TAKE_ITEM":     _lua_take_item,
    "CRAFT_ITEM":    _lua_craft_item,
}


def translate_to_lua(
    actions: list[dict],
    max_actions: int = 20,
) -> str:
    """
    Translate a validated list of primitive action dicts into
    a single Lua script. Returns the full Lua string.
    """
    parts = [_LUA_HEADER.replace('{max_actions}', str(max_actions))]

    for i, action in enumerate(actions):
        action_type = action["type"]
        translator  = _TRANSLATORS[action_type]
        parts.append(translator(action, i))

    parts.append(_LUA_FOOTER)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Result parsing
# ---------------------------------------------------------------------------

@dataclass
class ActionResult:
    index: int
    action_type: str
    ok: bool
    detail: str
    raw: str


@dataclass
class ExecutionReport:
    """
    Structured report produced by parse_execution_output().
    This is what gets stored in episodic memory and shown to the verifier.
    """
    success: bool                            # True if all actions succeeded
    actions_taken: list[ActionResult]        = field(default_factory=list)
    errors: list[ActionResult]               = field(default_factory=list)
    budget_exceeded: bool                    = False
    raw_output: str                          = ""

    def summary(self) -> str:
        lines = []
        lines.append(f"success: {self.success}")
        if self.actions_taken:
            lines.append("actions_taken:")
            for r in self.actions_taken:
                lines.append(f"  - {r.action_type}: {r.detail}")
        if self.errors:
            lines.append("errors:")
            for r in self.errors:
                lines.append(f"  - {r.action_type}: {r.detail}")
        if self.budget_exceeded:
            lines.append("note: execution budget was hit — some actions skipped")
        return "\n".join(lines)


def parse_execution_output(raw_output: str) -> ExecutionReport:
    """
    Parse the structured output lines printed by the translated Lua.

    Line formats:
        OK:{idx}:{TYPE}:{detail}
        ERR:{idx}:{TYPE}:{reason}:{detail}
        BUDGET_EXCEEDED:{message}
    """
    report = ExecutionReport(success=True, raw_output=raw_output)

    for line in raw_output.strip().splitlines():
        line = line.strip()
        if not line:
            continue

        if line.startswith("BUDGET_EXCEEDED:"):
            report.budget_exceeded = True
            continue

        parts = line.split(":", 3)
        if len(parts) < 3:
            continue

        status     = parts[0]
        idx_str    = parts[1]
        action_type = parts[2]
        detail     = parts[3] if len(parts) > 3 else ""

        try:
            idx = int(idx_str)
        except ValueError:
            idx = -1

        if status == "OK":
            report.actions_taken.append(ActionResult(
                index=idx, action_type=action_type,
                ok=True, detail=detail, raw=line,
            ))
        elif status == "ERR":
            report.success = False
            report.errors.append(ActionResult(
                index=idx, action_type=action_type,
                ok=False, detail=detail, raw=line,
            ))

    return report


# ---------------------------------------------------------------------------
# Executor output schema (what the LLM must return)
# ---------------------------------------------------------------------------

EXECUTOR_OUTPUT_SCHEMA = """\
You must respond with a valid JSON object with this exact structure:

{
  "reasoning": [
    "<why this sequence of actions achieves the task>"
  ],
  "actions": [
    {
      "type": "PLACE_ENTITY",
      "entity_name": "small-electric-pole",
      "x": 41.5,
      "y": 27.5,
      "direction": "NORTH"
    },
    {
      "type": "INSERT_ITEM",
      "entity_x": 43.0,
      "entity_y": 30.0,
      "item_name": "coal",
      "count": 10
    }
  ]
}

Available action types and their required fields:

PLACE_ENTITY:   entity_name (str), x (float), y (float), direction (NORTH/EAST/SOUTH/WEST)
REMOVE_ENTITY:  x (float), y (float)
ROTATE_ENTITY:  x (float), y (float), direction (NORTH/EAST/SOUTH/WEST)
INSERT_ITEM:    entity_x (float), entity_y (float), item_name (str), count (int)
TAKE_ITEM:      entity_x (float), entity_y (float), item_name (str), count (int)
CRAFT_ITEM:     item_name (str), count (int)

Rules:
- Use ONLY the actions listed above. No other actions exist.
- x, y coordinates come from the task context. Do not invent positions.
- direction must be exactly one of: NORTH, EAST, SOUTH, WEST
- count must be a positive integer
- Respond with JSON only. No explanation outside the JSON object.
"""


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    actions = [
        {
            "type": "PLACE_ENTITY",
            "entity_name": "small-electric-pole",
            "x": 41.5,
            "y": 27.5,
            "direction": "NORTH",
        },
        {
            "type": "PLACE_ENTITY",
            "entity_name": "small-electric-pole",
            "x": 44.5,
            "y": 27.5,
            "direction": "NORTH",
        },
        {
            "type": "INSERT_ITEM",
            "entity_x": 43.0,
            "entity_y": 30.0,
            "item_name": "coal",
            "count": 10,
        },
    ]

    errors = validate_actions(actions)
    if errors:
        print("VALIDATION ERRORS:")
        for e in errors:
            print(f"  {e}")
    else:
        print("Validation: OK")
        lua = translate_to_lua(actions, max_actions=20)
        print("\n--- Generated Lua ---")
        print(lua)

    # Test result parsing
    fake_output = """OK:0:PLACE_ENTITY:small-electric-pole:(41.5,27.5)
OK:1:PLACE_ENTITY:small-electric-pole:(44.5,27.5)
ERR:2:INSERT_ITEM:no_item:coal not in inventory"""

    print("\n--- Parsed execution report ---")
    report = parse_execution_output(fake_output)
    print(report.summary())