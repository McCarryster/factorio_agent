"""
agent_loop.py

Main loop connecting:
    Observe → Planner → Executor → Verifier → Memory → Repeat

Usage:
    python3 agent_loop.py
"""

from __future__ import annotations
import time
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
import factorio_rcon

from agent.planner import choose_next_action, EpisodicMemory, ActionRecord
from agent.executor import ExecutorAgent
from agent.verifier import Verifier
from observability.build_observation import get_planner_context
from metrics.production_tracker import ProductionTracker


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GOAL = "Build fully automated iron plate production."
MAX_ITERATIONS  = 30
MAX_ACTIONS_PER_STEP = 10
OBSERVE_WAIT_SEC = 2   # seconds between executor and verifier observe
ITER_WAIT_SEC    = 1   # seconds between loop iterations


# ---------------------------------------------------------------------------
# Loop state
# ---------------------------------------------------------------------------

@dataclass
class LoopState:
    iteration: int = 0
    status: str = "running"   # running / done / failed / limit
    last_action_type: str = ""
    last_verified: bool = False


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run(
    anthropic_client: anthropic.Anthropic,
    factorio_client: factorio_rcon.RCONClient,
    goal: str = GOAL,
    max_iterations: int = MAX_ITERATIONS,
) -> LoopState:

    production_tracker = ProductionTracker()
    memory   = EpisodicMemory(goal=goal)
    executor = ExecutorAgent(
        anthropic_client=anthropic_client,
        factorio_client=factorio_client,
        max_actions=MAX_ACTIONS_PER_STEP,
    )
    verifier = Verifier(
        factorio_client=factorio_client,
        production_tracker=production_tracker,
        goal=goal,
    )
    state = LoopState()

    print(f"\n{'='*60}")
    print(f"GOAL: {goal}")
    print(f"{'='*60}\n")

    while state.iteration < max_iterations:
        state.iteration += 1
        print(f"\n--- Iteration {state.iteration}/{max_iterations} ---")

        # ------------------------------------------------------------------
        # 1. OBSERVE — build semantic world model
        # ------------------------------------------------------------------
        world_summary = get_planner_context(
            client=factorio_client,
            production_tracker=production_tracker,
            skills_dir=Path("."),
            current_task=goal,
        )

        # ------------------------------------------------------------------
        # 2. CHECK GOAL — are we done?
        # ------------------------------------------------------------------
        if verifier.check_goal_achieved():
            # If the last action was recorded as failed but goal is now achieved,
            # correct the record — the action worked, verification condition was wrong.
            if memory.failed and not memory.completed:
                last = memory.failed[-1]
                memory.failed.pop()
                last.outcome = "completed"
                last.failure_reason = ""
                memory.completed.append(last)
            print("\n✓ GOAL ACHIEVED — iron plate production running with no failures.")
            state.status = "done"
            break

        # ------------------------------------------------------------------
        # 3. PLAN — choose next atomic action
        # ------------------------------------------------------------------
        print("Planning...")
        try:
            action = choose_next_action(
                client=anthropic_client,
                world_summary=world_summary,
                memory=memory,
            )
        except Exception as e:
            print(f"Planner error: {e}")
            state.status = "failed"
            break

        print(f"Action: {action.action} → {action.target}")
        print(f"Reasoning: {action.reasoning[0] if action.reasoning else '—'}")
        print(f"Success condition: {action.success_condition}")

        state.last_action_type = action.action

        # ------------------------------------------------------------------
        # 4. EXECUTE
        # ------------------------------------------------------------------
        print("Executing...")

        # Build context string from the action for the executor
        context = _build_executor_context(action, world_summary)

        exec_result = executor.run(task=action.action, context=context)

        if exec_result.report:
            print(f"Executor: {exec_result.report.summary()}")
        if exec_result.parse_error:
            print(f"Executor parse error: {exec_result.parse_error}")

        # ------------------------------------------------------------------
        # 5. WAIT — let game state settle
        # ------------------------------------------------------------------
        time.sleep(OBSERVE_WAIT_SEC)

        # ------------------------------------------------------------------
        # 6. VERIFY — independent world re-observation
        # ------------------------------------------------------------------
        print("Verifying...")
        verification = verifier.check(
            success_condition=action.success_condition,
            planner_action=action,
        )
        print(f"Verification: {verification}")
        state.last_verified = verification.verified

        # ------------------------------------------------------------------
        # 7. MEMORY UPDATE
        # ------------------------------------------------------------------
        record = ActionRecord(
            action=action.action,
            target=action.target,
            parameters=action.parameters,
            success_condition=action.success_condition,
            outcome="completed" if verification.verified else "failed",
        )

        if verification.verified:
            memory.record_completed(record)
            print(f"✓ Step complete: {action.action}")
        else:
            failure_reason = _extract_failure_reason(exec_result, verification)
            memory.record_failed(record, reason=failure_reason)
            print(f"✗ Step failed: {action.action} — {failure_reason}")

            # Stop if the exact same action+target failed 3 times in a row
            recent_same = [
                r for r in memory.failed[-5:]
                if r.action == action.action and r.target == action.target
            ]
            if len(recent_same) >= 3:
                print(f"✗ {action.action} → {action.target} failed 3 times — trying different approach.")
                # Don't stop — let planner try something else
                # Only stop if ALL recent failures are the same
                if len(memory.failed) >= 5 and len(set(
                    (r.action, r.target) for r in memory.failed[-5:]
                )) == 1:
                    print(f"✗ No progress in 5 iterations — stopping.")
                    state.status = "failed"
                    break

        time.sleep(ITER_WAIT_SEC)

    else:
        state.status = "limit"
        print(f"\n✗ Reached iteration limit ({max_iterations}).")

    # Final summary
    print(f"\n{'='*60}")
    print(f"Loop ended: {state.status}")
    print(f"Completed steps: {len(memory.completed)}")
    print(f"Failed steps: {len(memory.failed)}")
    for r in memory.completed:
        print(f"  ✓ {r.action} → {r.target}")
    for r in memory.failed:
        print(f"  ✗ {r.action} → {r.target}  ({r.failure_reason})")
    print(f"{'='*60}\n")

    return state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_executor_context(action, world_summary: str) -> str:
    """
    Build the context string for the executor from a PlannerAction.
    Includes the action's parameters and relevant world model excerpts.

    The executor must use ONLY the coordinates given here.
    It must not invent or adjust positions.
    """
    lines = []

    lines.append(f"Task: {action.action}")
    lines.append(f"Target: {action.target}")
    lines.append(f"Goal: {action.goal}")
    lines.append("")

    # Parameters from planner (includes suggested_position, gap info etc.)
    if action.parameters:
        lines.append("Parameters (use these exact values):")
        for k, v in action.parameters.items():
            lines.append(f"  {k}: {v}")
        lines.append("")

    # Pull suggested position and resource info from world model
    relevant = _extract_relevant_world_sections(world_summary)
    if relevant:
        lines.append("From world model:")
        lines.append(relevant)

    lines.append("")
    lines.append("IMPORTANT: Use ONLY the coordinates given above.")
    lines.append("Do NOT place entities at entity positions (inserters, furnaces, belts).")
    lines.append("Use the 'Suggested position' from REPAIR OPPORTUNITIES if available.")
    lines.append("")
    lines.append(f"Success condition: {action.success_condition}")

    return "\n".join(lines)


def _extract_relevant_world_sections(world_summary: str) -> str:
    """Extract repair opportunities and resources sections from world summary."""
    sections = []
    capture = False
    relevant_headers = {
        "=== REPAIR OPPORTUNITIES ===",
        "=== AVAILABLE RESOURCES ===",
        "=== ROOT CAUSE ANALYSIS ===",
    }

    current_section = []
    current_header  = None

    for line in world_summary.splitlines():
        if any(h in line for h in relevant_headers):
            if current_section and current_header:
                sections.append("\n".join(current_section))
            current_header  = line
            current_section = [line]
        elif line.startswith("===") and current_header:
            # New unrelated section — stop capturing
            if current_section:
                sections.append("\n".join(current_section))
            current_header  = None
            current_section = []
        elif current_header:
            current_section.append(line)

    if current_section and current_header:
        sections.append("\n".join(current_section))

    return "\n\n".join(sections)


def _extract_failure_reason(exec_result, verification) -> str:
    """Summarise why a step failed for episodic memory."""
    reasons = []

    if exec_result.parse_error:
        reasons.append(f"executor_error: {exec_result.parse_error[:80]}")

    if exec_result.report and exec_result.report.errors:
        for e in exec_result.report.errors[:2]:
            reasons.append(f"action_failed: {e.action_type} {e.detail[:60]}")

    if not verification.verified:
        reasons.append(f"verify_failed: {verification.reason[:80]}")

    return " | ".join(reasons) if reasons else "unknown"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from game_integration.dependencies import get_factorio_client
    from agent.dependencies import get_anthropic_client

    factorio_client  = get_factorio_client()
    anthropic_client = get_anthropic_client()

    run(
        anthropic_client=anthropic_client,
        factorio_client=factorio_client,
        goal=GOAL,
        max_iterations=MAX_ITERATIONS,
    )