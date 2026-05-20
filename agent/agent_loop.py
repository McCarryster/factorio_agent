"""
agent.py

Two-layer LLM agent for building an automated iron plate factory.

Reasoner: looks at game state + milestones, decides what to do next
Executor: translates the reasoner's intent into primitive calls

Each iteration:
  1. Observe game state
  2. Check goal
  3. Reasoner picks next action
  4. Executor generates primitive calls
  5. Execute primitives one by one
  6. Loop
"""

import json
import re
import time

import anthropic
from anthropic.types import MessageParam, Message, TextBlock
from agent.model_call import call_anthropic
from agent.dependencies import get_anthropic_client

# ---------------------------------------------------------------------------
# Milestones — the reasoner works through these in order
# ---------------------------------------------------------------------------

MILESTONES = """
MILESTONES (work through in order, skip if already done):

PHASE 1 — Manual bootstrap:
  M1. Mine ≥25 stone manually (for furnaces)
  M2. Craft 5 stone-furnace (costs 5 stone each)
  M3. Mine ≥30 coal manually (furnace fuel)
  M4. Mine ≥80 iron-ore manually
  M5. Smelt iron-ore → iron plates: place furnace, insert coal+ore, wait, TAKE plates out
      Need ≥50 iron plates TOTAL IN INVENTORY → unlocks steam-power tech automatically
      IMPORTANT: After wait_until succeeds, call take_item to collect plates from furnace!
      Repeat: mine ore → insert → wait → take_item → until inventory has 50 iron-plate
  M6. Mine ≥15 copper-ore manually
  M7. Smelt copper-ore → copper plates: need ≥10 IN INVENTORY → unlocks electronics tech
      IMPORTANT: take_item to collect copper plates from furnace after smelting!

PHASE 2 — Craft automation components:
  M8.  Craft iron-gear-wheels as needed (2 iron-plate → 1 gear)
  M9.  Craft ≥7 burner-mining-drill (3 iron-plate + 3 iron-gear + 1 stone-furnace each)
  M10. Craft ≥40 transport-belt (1 iron-plate + 1 iron-gear → 2 belts)
  M11. Craft ≥15 inserter (1 iron-plate + 1 iron-gear + 1 electronic-circuit each)
       electronic-circuit = 1 iron-plate + 3 copper-cable (2 cable per copper-plate)
  M12. Craft ≥10 pipe (1 iron-plate each)
  M13. Craft 1 boiler (4 pipe + 1 stone-furnace)
  M14. Craft 1 steam-engine (10 iron-plate + 8 iron-gear + 5 pipe)
  M15. Craft 1 offshore-pump (2 iron-gear + 3 pipe)
  M16. Craft ≥20 small-electric-pole (1 wood + 2 copper-cable each — need wood!)
       Chop trees with manual_mining(tree_x, tree_y, 1)
  M17. Craft 1 iron-chest (8 iron-plate)
  M18. Craft ≥5 stone-furnace for smelting block (5 stone each)

PHASE 3 — Place steam power:
  M19. find_water → call place_steam_network factory block

PHASE 4 — Place factory:
  M20. find_ore_patch("coal") → call place_coal_mining factory block (2 drills)
  M21. find_ore_patch("iron-ore") → call place_iron_mining factory block (5 drills)
  M22. call place_smelting factory block (5 furnaces)
  M23. Place belt tiles connecting coal output to main iron belt right lane
  M24. Place electric poles connecting steam engine to factory
  M25. Verify all running → DONE
"""

RECIPES_TEXT = """
RECIPES (enabled=✓ from start, ✗=locked until research):
  ✓ stone-furnace:       5 stone → 1 stone-furnace
  ✓ iron-gear-wheel:     2 iron-plate → 1 iron-gear-wheel
  ✓ transport-belt:      1 iron-plate + 1 iron-gear-wheel → 2 transport-belt
  ✓ burner-mining-drill: 3 iron-plate + 3 iron-gear-wheel + 1 stone-furnace → 1 drill
  ✓ iron-chest:          8 iron-plate → 1 iron-chest
  ✓ wooden-chest:        2 wood → 1 wooden-chest
  ✗ copper-cable:        1 copper-plate → 2 copper-cable  [needs: 10 copper plates smelted]
  ✗ electronic-circuit:  1 iron-plate + 3 copper-cable → 1 circuit  [needs: electronics]
  ✗ inserter:            1 iron-plate + 1 iron-gear-wheel + 1 electronic-circuit → 1 inserter
  ✗ small-electric-pole: 1 wood + 2 copper-cable → 1 small-electric-pole
  ✗ pipe:                1 iron-plate → 1 pipe  [needs: steam-power]
  ✗ boiler:              4 pipe + 1 stone-furnace → 1 boiler
  ✗ steam-engine:        10 iron-plate + 8 iron-gear-wheel + 5 pipe → 1 steam-engine
  ✗ offshore-pump:       2 iron-gear-wheel + 3 pipe → 1 offshore-pump

RESEARCH TRIGGERS (automatic, no science packs needed):
  steam-power unlocked after: smelting 50 iron plates total
  electronics unlocked after: smelting 10 copper plates total
"""

FACTORY_BLOCKS_TEXT = """
FACTORY BLOCK FUNCTIONS (call these in Phase 4 — they place everything automatically):

place_coal_mining(drill_x, drill_y, num_drills=2)
  Places coal drills + collector belt + output belt + self-sustaining inserters
  Requires in inventory: num_drills burner-mining-drill, ~10 transport-belt, num_drills inserter

place_iron_mining(drill_x, drill_y, num_drills=5)
  Places iron drills + main belt + coal feed inserters
  Requires: num_drills burner-mining-drill, ~20 transport-belt, num_drills+1 inserter

place_smelting(furnace_x, furnace_y, num_furnaces=5)
  Places furnaces + input/output inserters + output belt + chest
  furnace_y = iron_drill_y - 4
  Requires: num_furnaces stone-furnace, ~20 transport-belt, num_furnaces*2 inserter, 1 iron-chest

place_steam_network(water_x, water_y, pump_direction="WEST")
  Places offshore-pump + pipe + boiler + steam-engine + pole
  Requires: 1 offshore-pump, 1 boiler, 1 steam-engine, 3 pipe, 1 small-electric-pole, 50 coal

place_power_line(from_x, from_y, to_x, to_y, pole_spacing=7)
  Places chain of small-electric-poles from power source to factory
  Requires: ~20 small-electric-pole
"""

REASONER_SYSTEM = f"""
You are the reasoning layer of a Factorio agent building an automated iron plate factory.

You receive the current game state and decide what to do next.
Output a JSON object with:
{{
  "milestone": "M1",           // which milestone you're working on
  "reasoning": "...",          // why you chose this
  "intent": "mine 20 stone at (15,3)",  // plain English description of the action
  "action_type": "primitive" | "factory_block",
  "details": {{...}}           // specifics for the executor
}}

{MILESTONES}

{RECIPES_TEXT}

{FACTORY_BLOCKS_TEXT}

IMPORTANT RULES:
- Only craft items whose recipes are unlocked (check RECIPES UNLOCKED section)
- Only craft items you have ingredients for (check INVENTORY section)
- manual_mining max 20 per call, you may need multiple calls
- Keep track of what you need: plan ahead for the full factory
- For smelting: place furnace with place_entity, insert coal+ore with insert_item, wait with wait_until
- For factory blocks: use the factory block functions, not individual place_entity calls
- A stone-furnace consumes both fuel (coal) and input (ore) — insert both separately
""".strip()

EXECUTOR_SYSTEM = """
You are the execution layer of a Factorio agent.
You receive an intent and must output a JSON list of primitive calls to execute it.

Output format:
[
  {"primitive": "craft_item", "args": {"name": "stone-furnace", "count": 3}},
  {"primitive": "manual_mining", "args": {"x": 15, "y": 3, "count": 20}},
  ...
]

Or for factory blocks:
[
  {"primitive": "factory_block", "args": {"function": "place_coal_mining", "kwargs": {"drill_x": 94, "drill_y": -2, "num_drills": 2}}}
]

Available primitives and their signatures:
  place_entity(name, x, y, direction)       — place from inventory
  remove_entity(x, y)                       — remove to inventory
  rotate_entity(x, y, direction)            — rotate entity
  craft_item(name, count)                   — instant craft
  take_item(from_x, from_y, item, count)    — take from entity into inventory
                                              ALWAYS call this after wait_until to collect plates!
                                              e.g. after furnace produces iron-plate, take_item(fx, fy, "iron-plate", 20)
  insert_item(into_x, into_y, item, count)  — insert into entity
  manual_mining(x, y, count, resource="")  — mine resource near (x,y), pass resource name! (max 20)
                                              ALWAYS pass resource= to mine the correct ore type
                                              e.g. {"primitive":"manual_mining","args":{"x":0,"y":0,"count":20,"resource":"stone"}}
  wait_progress(seconds)                   — wait N seconds (max 30)
  wait_until(entity_x, entity_y, has_item, count, timeout_seconds)
  find_ore_patch(resource, near_x, near_y)  — returns positions
  find_water(near_x, near_y)               — returns water positions
  factory_block(function, kwargs)           — call a factory block

Output ONLY the JSON list, no explanation.
""".strip()


# ---------------------------------------------------------------------------
# Reasoner
# ---------------------------------------------------------------------------

def reason(client: anthropic.Anthropic, state_text: str, goal_status: str,
           history: list[str]) -> dict:
    """Call the reasoner LLM. Returns parsed JSON dict."""
    history_text = "\n".join(history[-6:]) if history else "(none)"

    user_msg = f"""
GOAL STATUS:
{goal_status}

GAME STATE:
{state_text}

RECENT ACTIONS:
{history_text}

What should I do next? Output JSON only.
""".strip()

    response = call_anthropic(
        client=client,
        prompt=REASONER_SYSTEM,
        history=[{"role": "user", "content": user_msg}],
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

def execute_intent(client: anthropic.Anthropic, intent: str,
                   state_text: str, details: dict) -> list[dict]:
    """Call executor LLM. Returns list of primitive call dicts."""
    user_msg = f"""
INTENT: {intent}

DETAILS: {json.dumps(details, indent=2)}

CURRENT STATE:
{state_text}

Output the JSON list of primitive calls to execute this intent.
""".strip()

    response = call_anthropic(
        client=client,
        prompt=EXECUTOR_SYSTEM,
        history=[{"role": "user", "content": user_msg}],
    )
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Primitive dispatcher
# ---------------------------------------------------------------------------

def dispatch(factorio_client, call: dict) -> dict:
    """
    Execute one primitive call.
    call = {"primitive": "craft_item", "args": {...}}
    """
    from game_integration.primitives_new import (
        place_entity, remove_entity, rotate_entity,
        craft_item, take_item, insert_item,
        manual_mining, wait_progress, wait_until,
        find_ore_patch, find_water,
    )
    from game_integration.factory_blocks import (
        place_coal_mining, place_iron_mining, place_smelting,
        place_steam_network, place_power_line,
        find_patch_edge, max_drills_on_patch,
    )
    from game_integration.primitives import validate_actions, translate_to_lua, parse_execution_output
    from game_integration.factorio_bridge import execute_lua

    prim = call.get("primitive")
    args = call.get("args", {})

    PRIMITIVE_MAP = {
        "place_entity":   place_entity,
        "remove_entity":  remove_entity,
        "rotate_entity":  rotate_entity,
        "craft_item":     craft_item,
        "take_item":      take_item,
        "insert_item":    insert_item,
        "manual_mining":  manual_mining,
        "wait_progress":  wait_progress,
        "wait_until":     wait_until,
        "find_ore_patch": find_ore_patch,
        "find_water":     find_water,
    }

    if prim == "factory_block":
        fn_name = args.get("function")
        kwargs  = args.get("kwargs", {})

        BLOCK_MAP = {
            "place_coal_mining":  place_coal_mining,
            "place_iron_mining":  place_iron_mining,
            "place_smelting":     place_smelting,
            "place_steam_network": place_steam_network,
            "place_power_line":   place_power_line,
        }
        fn = BLOCK_MAP.get(fn_name)
        if not fn:
            return {"success": False, "message": f"Unknown factory block: {fn_name}"}

        # Give player required items
        _give_items_for_block(factorio_client, fn_name, kwargs)

        actions, _ = fn(**kwargs)
        errs = validate_actions(actions)
        if errs:
            return {"success": False, "message": f"Validation: {errs[:2]}"}
        lua = translate_to_lua(actions, max_actions=300)
        result = execute_lua(factorio_client, lua)
        report = parse_execution_output(result["output"])
        return {
            "success": report.success,
            "message": f"{fn_name}: {len(report.actions_taken)} placed, "
                       f"{len(report.errors)} errors",
            "data": {"errors": [str(e) for e in report.errors[:3]]},
        }

    fn = PRIMITIVE_MAP.get(prim)
    if not fn:
        return {"success": False, "message": f"Unknown primitive: {prim}"}

    # Sensing primitives return lists, not Result dicts — wrap them
    if prim in ("find_ore_patch", "find_water"):
        data = fn(factorio_client, **args)
        if data:
            return {"success": True, "message": f"found {len(data)} positions", "data": {"positions": data}}
        return {"success": False, "message": f"nothing found for {args}", "data": {"positions": []}}

    # All other primitives return Result dicts directly
    return fn(factorio_client, **args)


def _give_items_for_block(client, fn_name: str, kwargs: dict):
    """Give player items needed for a factory block (best-effort)."""
    from game_integration.factorio_bridge import execute_lua
    from recipes import (requirements_coal_mining, requirements_iron_mining,
                         requirements_smelting, requirements_steam_network,
                         requirements_power_line)

    req_fns = {
        "place_coal_mining":   lambda: requirements_coal_mining(kwargs.get("num_drills", 2)),
        "place_iron_mining":   lambda: requirements_iron_mining(kwargs.get("num_drills", 5)),
        "place_smelting":      lambda: requirements_smelting(kwargs.get("num_furnaces", 5)),
        "place_steam_network": requirements_steam_network,
        "place_power_line":    lambda: requirements_power_line(25),
    }
    req_fn = req_fns.get(fn_name)
    if not req_fn:
        return

    reqs = req_fn()
    inserts = "\n".join(
        f'game.players[1].insert{{name="{k}", count={v}}}'
        for k, v in reqs.items()
    )
    execute_lua(client, inserts)


# ---------------------------------------------------------------------------
# Main agent loop
# ---------------------------------------------------------------------------

def run(
    factorio_client,
    anthropic_client: anthropic.Anthropic,
    max_iterations: int = 50,
):
    from observation import format_state
    from goal import check_goal, format_goal_status, GOAL_DESCRIPTION

    print("=" * 60)
    print("GOAL:", GOAL_DESCRIPTION.strip())
    print("=" * 60)

    history: list[str] = []

    for iteration in range(1, max_iterations + 1):
        print(f"\n--- Iteration {iteration}/{max_iterations} ---")

        # 1. Observe
        state_text  = format_state(factorio_client)
        goal_status = format_goal_status(factorio_client)

        # 2. Check goal
        done, goal_reason = check_goal(factorio_client)
        if done:
            print(f"\n✓ {goal_reason}")
            return True

        # 3. Reason
        print("Reasoning...")
        try:
            plan = reason(anthropic_client, state_text, goal_status, history)
        except Exception as e:
            import traceback
            print(f"Reasoner error: {e}")
            print(traceback.format_exc())
            history.append(f"[ERROR] Reasoner failed: {e}")
            continue

        milestone = plan.get("milestone", "?")
        reasoning = plan.get("reasoning", "")
        intent    = plan.get("intent", "")
        details   = plan.get("details", {})

        print(f"Milestone: {milestone}")
        print(f"Reasoning: {reasoning}")
        print(f"Intent:    {intent}")

        # 4. Execute
        print("Executing...")
        try:
            calls = execute_intent(anthropic_client, intent, state_text, details)
        except Exception as e:
            print(f"Executor error: {e}")
            history.append(f"[{milestone}] {intent} → executor error: {e}")
            continue

        print(f"Calls: {len(calls)}")
        results = []
        for i, call in enumerate(calls):
            prim = call.get("primitive", "?")
            args = call.get("args", {})
            print(f"  [{i+1}] {prim}({args})")
            try:
                result = dispatch(factorio_client, call)
            except Exception as e:
                result = {"success": False, "message": str(e)}
            status = "✓" if result.get("success") else "✗"
            print(f"      {status} {result.get('message', '')}")
            results.append((prim, result))
            if not result.get("success") and prim not in ("find_ore_patch", "find_water"):
                # Stop executing this batch on first failure (except sensing)
                print(f"      Stopping batch on failure")
                break

        # 5. Update history
        successes = sum(1 for _, r in results if r.get("success"))
        failures  = sum(1 for _, r in results if not r.get("success"))
        fail_msgs = [r.get("message","") for _, r in results if not r.get("success")]
        history.append(
            f"[{milestone}] {intent} → "
            f"{successes} ok, {failures} failed"
            + (f": {fail_msgs[0]}" if fail_msgs else "")
        )

        # Brief pause between iterations
        time.sleep(0.5)

    print(f"\nReached max iterations ({max_iterations})")
    return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from game_integration.dependencies import get_factorio_client
    from game_integration.factorio_bridge import execute_lua

    factorio = get_factorio_client()
    pong = execute_lua(factorio, "rcon.print('pong')")
    assert pong["output"].strip() == "pong", "Factorio connection failed"
    print("Connected to Factorio.")

    from dependencies import get_anthropic_client
    ai = get_anthropic_client()
    run(factorio, ai, max_iterations=50)