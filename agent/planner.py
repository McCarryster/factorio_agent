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
    "EXTEND_POWER_NETWORK": "Place electric poles to close a power coverage gap.",
    "INSERT_FUEL":          "Insert coal into burner entities that have run out.",
    "PLACE_ENTITY":         "Place a new entity (drill, furnace, inserter, belt).",
    "REMOVE_ENTITY":        "Remove a misplaced or blocking entity.",
    "ROTATE_ENTITY":        "Rotate an entity to fix wrong orientation.",
    "CONNECT_POWER":        "Connect an isolated pole into the main network.",
    "MOVE_TO":              "Move the player to a location.",
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
  "success_condition": "<how to verify this step worked, in plain English>"
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

== PRIORITY ORDER ==
When multiple failures exist, fix in this order:
  1. Power (EXTEND_POWER_NETWORK, CONNECT_POWER)
  2. Fuel  (INSERT_FUEL)
  3. Flow  (PLACE_ENTITY, ROTATE_ENTITY, REMOVE_ENTITY)
  4. Verify production is running (WAIT then check)
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
        parameters        = data.get("parameters", {}),
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

    from agent.dependencies import get_anthropic_client
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