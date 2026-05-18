"""
verifier.py

After the executor runs, the verifier independently re-observes the world
and checks whether the planner's success condition was met.

It never trusts the executor's self-report. It always re-reads game state.

Usage:
    from agent.verifier import Verifier, VerificationResult

    verifier = Verifier(factorio_client, production_tracker)
    result = verifier.check(
        success_condition="inserter@(41.5,27.5).status != NO_POWER",
        planner_action=action,
    )
    if result.verified:
        memory.record_completed(...)
    else:
        memory.record_failed(..., reason=result.reason)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import re

import factorio_rcon

from metrics.entity_status import get_entity_status
from metrics.production_tracker import ProductionTracker
from agent.observability.world_model import (
    build_semantic_world_model,
    SubsystemName,
    FailureType,
)


# ---------------------------------------------------------------------------
# Verification result
# ---------------------------------------------------------------------------

@dataclass
class VerificationResult:
    verified: bool
    reason: str
    world_summary: str = ""          # fresh world model after execution

    def __str__(self) -> str:
        status = "VERIFIED" if self.verified else "FAILED"
        return f"[{status}] {self.reason}"


# ---------------------------------------------------------------------------
# Condition checkers
# ---------------------------------------------------------------------------

def _parse_entity_condition(
    condition: str,
    entities: list[dict],
) -> Optional[bool]:
    """
    Parse conditions like:
        inserter@(41.5,27.5).status != NO_POWER
        stone-furnace@(45,30).status == WORKING
        inserter@(45.5,28.5).status != NO_POWER

    Returns True/False if matched, None if condition not parseable.
    """
    # Match: entity_name@(x,y).field op value
    m = re.match(
        r"^([\w\-]+)@\(([\d\.\-]+),([\d\.\-]+)\)\.([\w]+)\s*(==|!=)\s*(\w+)$",
        condition.strip()
    )
    if not m:
        return None

    entity_name = m.group(1)
    ex          = float(m.group(2))
    ey          = float(m.group(3))
    field_name  = m.group(4)
    operator    = m.group(5)
    value       = m.group(6)

    # Find the entity
    target = None
    for e in entities:
        if (entity_name in e["name"]
                and abs(e["x"] - ex) < 1.0
                and abs(e["y"] - ey) < 1.0):
            target = e
            break

    if target is None:
        return None  # entity not found — can't verify

    actual = str(target.get(field_name, ""))
    if operator == "==":
        return actual == value
    elif operator == "!=":
        return actual != value
    return None


def _parse_subsystem_condition(
    condition: str,
    world_summary: str,
) -> Optional[bool]:
    """
    Parse conditions like:
        smelting.operational > 0
        mining.operational == 8
        power.status == OK

    Reads from the world summary text since we don't expose the full model here.
    """
    m = re.match(
        r"^(\w+)\.(operational|status)\s*(>|==|!=|>=)\s*(\w+)$",
        condition.strip()
    )
    if not m:
        return None

    subsystem = m.group(1).lower()
    field     = m.group(2)
    operator  = m.group(3)
    value     = m.group(4)

    # Find the subsystem line in the world summary
    # Format: [OK      ] smelting  (6/9 operational)
    pattern = rf"\[(\w+)\s*\]\s+{subsystem}\s+\((\d+)/(\d+) operational\)"
    sm = re.search(pattern, world_summary, re.IGNORECASE)
    if not sm:
        # Subsystem absent (no entities) — treat as 0 operational
        operational = 0
        status_str = "ABSENT"
        if field == "operational":
            try:
                rhs = int(value)
            except ValueError:
                return None
            if operator == ">":  return operational > rhs
            elif operator == ">=": return operational >= rhs
            elif operator == "==": return operational == rhs
            elif operator == "!=": return operational != rhs
        return None

    status_str   = sm.group(1).strip()
    operational  = int(sm.group(2))
    total        = int(sm.group(3))

    if field == "operational":
        try:
            rhs = int(value)
        except ValueError:
            return None
        if operator == ">":
            return operational > rhs
        elif operator == ">=":
            return operational >= rhs
        elif operator == "==":
            return operational == rhs
        elif operator == "!=":
            return operational != rhs

    elif field == "status":
        if operator == "==":
            return status_str.upper() == value.upper()
        elif operator == "!=":
            return status_str.upper() != value.upper()

    return None


def _check_no_critical_failures(world_summary: str) -> bool:
    """True if world model reports no critical failures."""
    return "No critical failures detected." in world_summary


def _check_production_running(world_summary: str) -> bool:
    """True if iron plate production rate > 0."""
    m = re.search(r"Iron plate production:\s*([\d.]+)/sec", world_summary)
    if m:
        return float(m.group(1)) > 0.0
    return False


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------

class Verifier:
    def __init__(
        self,
        factorio_client: factorio_rcon.RCONClient,
        production_tracker: ProductionTracker,
        goal: str = "Build fully automated iron plate production.",
    ):
        self.factorio_client   = factorio_client
        self.production_tracker = production_tracker
        self.goal              = goal

    def _observe(self) -> tuple[list[dict], dict[str, float], dict[str, int], str]:
        """Re-observe the full world state. Returns (entities, throughput, inventory, world_summary)."""
        from metrics.entity_status import get_entity_status
        from observability.build_observation import get_player_inventory
        from metrics.technologies import get_technologies_researched

        entities  = get_entity_status(self.factorio_client)
        self.production_tracker.update(entities)
        throughput = self.production_tracker.get_throughput()
        inventory  = get_player_inventory(self.factorio_client)
        techs      = get_technologies_researched(self.factorio_client)

        world_summary = build_semantic_world_model(
            raw_entities=entities,
            throughput=throughput,
            inventory=inventory,
            technologies=techs,
            goal=self.goal,
        )
        return entities, throughput, inventory, world_summary

    def check(
        self,
        success_condition: str,
        planner_action=None,     # PlannerAction — used for logging only
    ) -> VerificationResult:
        """
        Re-observe the world and check the success condition.

        Condition formats supported:
            entity@(x,y).status != NO_POWER
            smelting.operational > 0
            no_critical_failures
            production_running
        """
        entities, throughput, inventory, world_summary = self._observe()

        condition = success_condition.strip()

        # --- Special conditions ---
        if condition == "no_critical_failures":
            ok = _check_no_critical_failures(world_summary)
            reason = "No critical failures detected." if ok \
                     else "Critical failures still present."
            return VerificationResult(
                verified=ok, reason=reason, world_summary=world_summary
            )

        if condition == "production_running":
            # Rate measurement needs multiple observations to be meaningful.
            # If tracker hasn't accumulated enough data, report as not yet measurable.
            obs = getattr(self.production_tracker, "update_count", None)
            if obs is not None and obs < 3:
                return VerificationResult(
                    verified=False,
                    reason=f"Production rate not yet measurable ({obs}/3 observations). "
                           f"Re-check after more loop iterations.",
                    world_summary=world_summary,
                )
            ok = _check_production_running(world_summary)
            reason = "Iron plate production is running." if ok \
                     else "Iron plate production is still 0/sec."
            return VerificationResult(
                verified=ok, reason=reason, world_summary=world_summary
            )

        # --- Entity conditions ---
        entity_result = _parse_entity_condition(condition, entities)
        if entity_result is not None:
            reason = f"Condition '{condition}' → {'met' if entity_result else 'NOT met'}"
            return VerificationResult(
                verified=entity_result, reason=reason, world_summary=world_summary
            )

        # --- Subsystem conditions ---
        subsystem_result = _parse_subsystem_condition(condition, world_summary)
        if subsystem_result is not None:
            reason = f"Condition '{condition}' → {'met' if subsystem_result else 'NOT met'}"
            return VerificationResult(
                verified=subsystem_result, reason=reason, world_summary=world_summary
            )

        # --- Could not parse ---
        return VerificationResult(
            verified=False,
            reason=f"Could not parse success condition: '{condition}'",
            world_summary=world_summary,
        )

    def check_goal_achieved(self) -> bool:
        """
        Check whether the top-level goal is complete.

        We consider the goal achieved when:
        - No critical failures exist
        - All key subsystems are operational

        We do NOT require production_running > 0 here because the
        ProductionTracker needs multiple observations to report a non-zero
        rate. The loop will detect sustained production separately.
        """
        _, _, _, world_summary = self._observe()
        if not _check_no_critical_failures(world_summary):
            return False
        # Check smelting is actually running
        smelting_ok = _parse_subsystem_condition("smelting.operational > 0", world_summary)
        mining_ok   = _parse_subsystem_condition("mining.operational > 0", world_summary)
        return bool(smelting_ok and mining_ok)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from game_integration.dependencies import get_factorio_client
    from metrics.production_tracker import ProductionTracker

    client  = get_factorio_client()
    tracker = ProductionTracker()

    verifier = Verifier(client, tracker)

    print("=== VERIFIER TEST ===\n")

    conditions = [
        # These should reflect actual game state when run
        "inserter@(41.5,27.5).status != NO_POWER",
        "inserter@(45.5,28.5).status != NO_POWER",
        "stone-furnace@(45,30).status == WORKING",
        "smelting.operational > 0",
        "no_critical_failures",
        "production_running",
    ]

    for cond in conditions:
        result = verifier.check(cond)
        print(result)