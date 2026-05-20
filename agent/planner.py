"""
planner.py

Reads the semantic world model summary + episodic memory,
calls the LLM, and returns a single structured next action.

Output schema:
    {
        "goal":              str,
        "reasoning":         list[str],
        "action":            str,         # one of ACTION_TYPES
        "target":            str,         # semantic target e.g. "smelting_area"
        "parameters":        dict,        # action-specific extras
        "success_condition": str,         # how to verify this step worked
    }
"""

from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from typing import Optional
import anthropic


# ---------------------------------------------------------------------------
# Action vocabulary
# ---------------------------------------------------------------------------

ACTION_TYPES = {
    # --- Factory building blocks (use these when infrastructure is missing) ---
    "BUILD_COAL_MINING":    "Place coal drills + collector belt on coal patch.",
    "BUILD_IRON_MINING":    "Place iron drills + main belt on iron ore patch.",
    "BUILD_SMELTING":       "Place furnaces + inserters connected to iron mining belt.",
    "BUILD_STEAM_NETWORK":  "Place offshore pump + boiler + steam engine near water.",
    "BUILD_POWER_LINE":     "Place electric poles from steam engine to factory.",
    "CONNECT_COAL_BELT":    "Route belt from coal output to join iron ore belt right lane.",
    # --- Repair actions (use these when infrastructure exists but is broken) ---
    "EXTEND_POWER_NETWORK": "Place electric poles to close a power coverage gap.",
    "INSERT_FUEL":          "Insert coal into burner entities that have run out.",
    "PLACE_ENTITY":         "Place a new entity (drill, furnace, inserter, belt).",
    "REMOVE_ENTITY":        "Remove a misplaced or blocking entity.",
    "ROTATE_ENTITY":        "Rotate an entity to fix wrong orientation.",
    "CONNECT_POWER":        "Connect an isolated pole into the main network.",
    "CRAFT_ITEM":           "Craft items from available materials.",
    "TRANSFER_ITEM":        "Move items from inventory to an entity or chest.",
    "INSPECT_ENTITY":       "Observe the state of a specific entity.",
    "WAIT":                 "Wait for production to stabilise before acting.",
}


# ---------------------------------------------------------------------------
# Episodic memory
# ---------------------------------------------------------------------------

@dataclass
class ActionRecord:
    action: str
    target: str
    parameters: dict
    success_condition: str
    outcome: str        # "completed" | "failed" | "partial"
    failure_reason: str = ""
    retryable: bool     = True


@dataclass
class EpisodicMemory:
    goal: str = ""
    completed: list[ActionRecord] = field(default_factory=list)
    failed:    list[ActionRecord] = field(default_factory=list)
    current:   Optional[ActionRecord] = None

    def record_completed(self, record: ActionRecord) -> None:
        record.outcome = "completed"
        self.completed.append(record)
        self.current = None

    def record_failed(self, record: ActionRecord, reason: str) -> None:
        record.outcome = "failed"
        record.failure_reason = reason
        self.failed.append(record)
        self.current = None

    def serialize(self) -> str:
        """Render memory as text for the planner prompt."""
        lines = []

        lines.append(f"Current goal: {self.goal}")
        lines.append("")

        if self.completed:
            lines.append("Completed steps:")
            for r in self.completed[-5:]:   # last 5 only
                lines.append(f"  ✓ {r.action} → {r.target}")
        else:
            lines.append("Completed steps: none yet")

        lines.append("")

        if self.failed:
            lines.append("Failed attempts:")
            for r in self.failed[-3:]:      # last 3 only
                retry = "retryable" if r.retryable else "do not retry"
                lines.append(
                    f"  ✗ {r.action} → {r.target}  "
                    f"reason: {r.failure_reason}  ({retry})"
                )
        else:
            lines.append("Failed attempts: none")

        return "\n".join(lines)

    def recently_failed(self, action: str, target: str) -> bool:
        """True if this exact action+target failed in the last 3 attempts."""
        recent = self.failed[-3:]
        return any(r.action == action and r.target == target for r in recent)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are the Planner for a Factorio automation agent.

Your job is to read the current semantic world model and decide the single best next action to make progress toward the goal.

== ACTION VOCABULARY ==
You may only emit ONE of the following action types:

{action_list}

== OUTPUT FORMAT ==
You must respond with a valid JSON object and nothing else. No explanation outside the JSON.

{{
  "goal": "<short description of what you are trying to achieve>",
  "reasoning": [
    "<observation 1>",
    "<observation 2>",
    "<why this action is the best next step>"
  ],
  "action": "<ACTION_TYPE>",
  "target": "<semantic target: e.g. smelting_area, drill@(45,34), all_furnaces>",
  "parameters": {{
    "<key>": "<value>"
  }},
  "success_condition": "<ONE condition in EXACT format — see formats below>"

SUCCESS CONDITION FORMATS (use exactly one):
  Entity status:    inserter@(X,Y).status != NO_POWER
  Entity status:    stone-furnace@(X,Y).status == WORKING
  Subsystem count:  smelting.operational > 0
  Subsystem count:  mining.operational >= 4
  Special:          no_critical_failures
  Special:          production_running

Use coordinates from the world model exactly as written (e.g. inserter@(41.5,27.5)).
Do NOT write free-text conditions. The verifier can only parse the formats above.
}}

== RULES ==
1. Choose the action that unblocks the most downstream systems.
2. Fix ROOT CAUSES first, not symptoms.
   - If inserters have NO_POWER, fix the power gap — do not try to add ore.
3. If an action recently failed, do not retry the exact same action+target.
   Try a different approach or a different target.
4. WAIT only if production is running and you are waiting for throughput to stabilise.
5. Do not invent actions outside the vocabulary.
6. parameters may be empty {{}} if not needed.
7. target must be semantic (area name, entity label, subsystem name) — never raw coordinates.
   The executor skill layer resolves exact positions.

== PARAMETERS ==
Always include relevant coordinates in parameters so the executor has exact positions.
For EXTEND_POWER_NETWORK, include:
  "suggested_position": "X, Y"   ← use the 'Suggested position' from REPAIR OPPORTUNITIES
  "gap_tiles": N                  ← shortfall in tiles from Evidence line
  success_condition must be: smelting.operational > 0
  Never use a specific inserter status for EXTEND_POWER_NETWORK — the pole may not reach every consumer.
For INSERT_FUEL, include:
  "fuel_item": "coal"
  "target_entities": "entity@(X,Y), ..."
For PLACE_ENTITY, include:
  "position": "X, Y"
  "entity_name": "..."
  "direction": "NORTH"

== PRIORITY ORDER ==
When multiple failures exist, fix in this order:
  1. Power (EXTEND_POWER_NETWORK, CONNECT_POWER)
  2. Fuel  (INSERT_FUEL)
  3. Flow  (PLACE_ENTITY, ROTATE_ENTITY, REMOVE_ENTITY)
  4. If all subsystems show [OK] and no critical failures → emit WAIT, do nothing else

== FACTORY BUILDING ACTIONS ==

Use BUILD_* actions when subsystems are ABSENT (never built).
Use repair actions (EXTEND_POWER_NETWORK etc.) when subsystems exist but are broken.

BUILD_COAL_MINING:
  When: mining subsystem ABSENT or no coal drills exist
  parameters field MUST include: {{"near_x": X, "near_y": Y}}
  Use coordinates from "=== NEARBY ORE PATCHES ===" section for coal
  Success: mining.operational > 0

BUILD_IRON_MINING:
  When: mining subsystem ABSENT or no iron drills exist
  parameters field MUST include: {{"near_x": X, "near_y": Y}}
  Use coordinates from "=== NEARBY ORE PATCHES ===" section for iron-ore
  Success: mining.operational > 0

BUILD_SMELTING:
  When: smelting subsystem ABSENT
  Requires: iron mining already built (needs drill positions)
  Parameters: {{}}
  Success: smelting.operational > 0

BUILD_STEAM_NETWORK:
  When: power subsystem ABSENT (no steam engine)
  Parameters: {{}}  (finds water automatically)
  Success: power.operational > 0

BUILD_POWER_LINE:
  When: steam engine exists but factory has no power
  Parameters: {{}}  (routes from engine to factory automatically)
  Success: no_critical_failures OR power network reaches factory

CONNECT_COAL_BELT:
  When: main belt is SINGLE (iron-ore only) but coal drills exist
  Parameters: {{}}
  Success: main belt becomes DUAL_MIXED

== BUILD ORDER (nothing built yet) ==
When the factory is empty AND inventory has no coal, follow this sequence:

PHASE 1 — Get coal first (required for sustained production):
  1. PLACE_ENTITY burner-mining-drill on COAL patch (not iron ore)
     facing SOUTH — outputs to (drill_x, drill_y + 2)
  2. INSERT_ITEM wood into drill fuel slot
  3. WAIT a few seconds for coal to accumulate in output area
  4. TAKE_ITEM coal from drill output area (or wait for drill to fill up)

PHASE 2 — Build iron production:
  5. PLACE_ENTITY burner-mining-drill on IRON ORE patch, facing SOUTH
  6. INSERT_ITEM coal into drill fuel slot
  7. PLACE_ENTITY stone-furnace — center at EXACTLY (drill_x, drill_y + 2)
     (that is the drill output tile — ore drops directly into furnace)
  8. INSERT_ITEM coal into furnace fuel slot
  9. Verify smelting.operational > 0

CRITICAL POSITIONING RULES:
- Drill facing SOUTH: output tile is (drill_x, drill_y + 2)
- Drill facing NORTH: output tile is (drill_x, drill_y - 2)
- Stone furnace center must be at EXACTLY the output tile
- The item flows section shows "drill output tile: (X,Y)" — use those exact coordinates
- Do NOT use alt positions for furnace placement — exact position only
- Do NOT place transport-belt or inserter — not in inventory
- Do NOT try to craft coal — it cannot be crafted

INVENTORY CHECK:
If coal > 0 in inventory: skip Phase 1, go directly to Phase 2.
If wood > 0 but coal = 0: must do Phase 1 first.

== CRITICAL RULE: WHEN TO STOP ACTING ==
If the world model shows:
  - No critical failures
  - All subsystems [OK]
  - Item flows show no broken steps

Then the factory is working. Do NOT place new entities. Do NOT try to fix things.
Emit WAIT with success_condition: no_critical_failures

Iron plate production showing 0.00/sec is a MEASUREMENT DELAY, not a failure.
The production tracker needs time to accumulate data.
If subsystems are all OK, trust that and WAIT.
"""

def _build_action_list() -> str:
    return "\n".join(
        f"  {name}: {desc}"
        for name, desc in ACTION_TYPES.items()
    )


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

@dataclass
class PlannerAction:
    goal: str
    reasoning: list[str]
    action: str
    target: str
    parameters: dict
    success_condition: str

    def display(self) -> str:
        lines = []
        lines.append(f"goal: {self.goal}")
        lines.append("reasoning:")
        for r in self.reasoning:
            lines.append(f"  - {r}")
        lines.append(f"action: {self.action}")
        lines.append(f"target: {self.target}")
        if self.parameters:
            lines.append("parameters:")
            for k, v in self.parameters.items():
                lines.append(f"  {k}: {v}")
        lines.append(f"success_condition: {self.success_condition}")
        return "\n".join(lines)


def _extract_parameters(data: dict) -> dict:
    """Extract parameters from LLM output, handling various formats."""
    import json as _json
    params = data.get("parameters", {})
    if isinstance(params, str):
        try:
            params = _json.loads(params)
        except Exception:
            params = {}
    if not isinstance(params, dict):
        params = {}
    # Also absorb top-level keys that look like parameters
    for key in ("near_x", "near_y", "resource", "suggested_position",
                "gap_tiles", "suggested_x", "suggested_y"):
        if key in data and key not in params:
            params[key] = data[key]
    return params


def choose_next_action(
    client: anthropic.Anthropic,
    world_summary: str,
    memory: EpisodicMemory,
) -> PlannerAction:
    """
    Call the LLM planner with the current world model and episodic memory.
    Returns a structured PlannerAction.
    """
    from agent.model_call import call_anthropic

    system = SYSTEM_PROMPT.format(
        action_list=_build_action_list()
    )

    user_message = f"""\
=== CURRENT WORLD STATE ===
{world_summary}

=== EPISODIC MEMORY ===
{memory.serialize()}

Based on the world state and memory above, choose the single best next action.
Respond with JSON only.
"""

    response = call_anthropic(
        client=client,
        prompt=system,
        history=[{"role": "user", "content": user_message}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Planner returned invalid JSON:\n{raw}\n\nError: {e}")

    # Validate action type
    action = data.get("action", "")
    if action not in ACTION_TYPES:
        raise ValueError(
            f"Planner returned unknown action '{action}'. "
            f"Valid: {list(ACTION_TYPES.keys())}"
        )

    return PlannerAction(
        goal              = data.get("goal", ""),
        reasoning         = data.get("reasoning", []),
        action            = action,
        target            = data.get("target", ""),
        parameters        = _extract_parameters(data),
        success_condition = data.get("success_condition", ""),
    )


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Simulate the broken factory world model output
    BROKEN_FACTORY_WORLD_MODEL = """\
=== GOAL ===
Build fully automated iron plate production.

Current requirement: Restore iron ore flow and furnace throughput.
==================================================
=== FACTORY SUMMARY ===

Iron plate production: 0.00/sec

Subsystems:
  [DEGRADED] power    (21/22 operational)
  [DEGRADED] mining   (4/8 operational)
  [DOWN    ] smelting (0/9 operational)
  [OK      ] belts    (40/40 operational)
==================================================
=== ROOT CAUSE ANALYSIS ===

PRIMARY: POWER_NETWORK_GAP
  Power network does not reach all electric consumers.
  Evidence: Nearest connected pole is 12.5 tiles from inserter@(41.5,27.5)
            (supply radius 7.5 tiles — 5.0 tiles short)
  Affects: 11 entity/entities
  Effects:
    - smelting system inserters disabled
    - mining system inserters disabled
    - furnaces receive no ore → zero smelting
    - 4 drill(s) blocked (inserters have no power)

SECONDARY 1: NO_INGREDIENTS
  3 furnace(s) starved — no input ore.
==================================================
=== ITEM FLOWS (4 total, 3 broken) ===

Flow 1: [BROKEN]
    ✓ burner-mining-drill@(45,34)
    ✗ inserter@(45.5,28.5) [NO_POWER]   ← First break
    ✗ stone-furnace@(45,30) [NO_INGREDIENTS]
    ✗ inserter@(45.5,31.5) [NO_POWER]

Flow 2: [BROKEN]  (same pattern)
Flow 3: [BROKEN]  (same pattern)
Flow 4: [OK]
==================================================
=== REPAIR OPPORTUNITIES ===

Priority 1 [HIGH IMPACT / LOW COST]: place_electric_pole
  Extend power network to reach 11 unpowered consumers.
  Required items: small-electric-pole:3
==================================================
=== AVAILABLE RESOURCES ===
  coal: 50
  small-electric-pole: 3
  iron-plate: 120
==================================================
=== TECHNOLOGIES ===
  electronics
  steam-power
"""

    from game_integration.dependencies import get_anthropic_client
    client = get_anthropic_client()

    memory = EpisodicMemory(goal="Build fully automated iron plate production.")

    print("=== CALLING PLANNER ===\n")
    action = choose_next_action(client, BROKEN_FACTORY_WORLD_MODEL, memory)
    print(action.display())

    print("\n=== SIMULATING ONE FAILURE, REPLANNING ===\n")
    memory.record_failed(
        ActionRecord(
            action="EXTEND_POWER_NETWORK",
            target="smelting_area",
            parameters={},
            success_condition="smelting.operational > 0",
            outcome="failed",
        ),
        reason="tile blocked at suggested position",
    )

    action2 = choose_next_action(client, BROKEN_FACTORY_WORLD_MODEL, memory)
    print(action2.display())