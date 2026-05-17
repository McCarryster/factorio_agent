"""
TODO: description
"""


import factorio_rcon

import anthropic
from anthropic.types import MessageParam, Message, TextBlock

from enum import Enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langfuse import observe

import agent.cfg as cfg
from agent.prompt import executor_prompt, planner_prompt
from agent.model_call import call_anthropic
from agent.utils import parse_executor_agent_output, parse_planner_agent_output, save_skill, reuse_skill
from metrics.skill_reuse import record_execution
from metrics.skill_library import get_skill_names
from metrics.entity_status import get_entity_status, format_entity_status
from game_integration.lua_validator import validate_lua, has_blocking, format_issues
from game_integration.factorio_bridge import execute_lua
from observability.build_observation import build_observation
from metrics.production_tracker import ProductionTracker



# ============================================================
# Dataclasses for states
# ============================================================

class AgentStatus(Enum):
    RUNNING = "running"
    DONE    = "done"
    FAILED  = "failed"
    LIMIT   = "limit"

@dataclass
class ExecutorState:
    """State for one subtask execution."""
    task: str = "" # the subtask description
    history: list[MessageParam] = field(default_factory=list)
    iteration: int = 0
    status: AgentStatus = AgentStatus.RUNNING
    last_action: str | None = None
    last_observation: str | None = None

@dataclass
class PlannerState:
    goal: str = ""
    reasoning: str = ""
    plan: list[dict] = field(default_factory=list)
    status: AgentStatus = AgentStatus.RUNNING

@dataclass
class OrchestratorState:
    goal: str = ""
    plan: list[dict] = field(default_factory=list)
    completed_subtasks: list[dict] = field(default_factory=list)
    failed_subtasks: list[tuple[dict, str]] = field(default_factory=list)
    current_subtask_index: int = 0
    replan_count: int = 0
    max_replans: int = 3
    status: AgentStatus = AgentStatus.RUNNING

@dataclass
class VerifierState:
    subtask: str
    verified: bool = False
    reason: str = ""



# ============================================================
# Agents
# ============================================================

class ExecutorAgent:
    def __init__(self, anthropic_client: anthropic.Anthropic,
                 factorio_client: factorio_rcon.RCONClient,
                 production_tracker: ProductionTracker,
                 skills_dir: Path):
        self.anthropic_client: anthropic.Anthropic = anthropic_client
        self.factorio_client: factorio_rcon.RCONClient = factorio_client
        self.production_tracker: ProductionTracker  = production_tracker
        self.skills_dir: Path = skills_dir

    def _windowed_history(self, history: list, window: int | None) -> list:
        """Return [first_msg] + last `window` messages, or full history if window is None."""
        if window is None or len(history) <= window + 1:
            return history
        return [history[0]] + history[-window:]

    @observe(name="executor_agent_run")
    def run(self, task: str, max_iterations: int, history_window: int) -> ExecutorState:
        # Agent initialization
        state = ExecutorState(task=task)
        state.history.append({
            "role": "user",
            "content": build_observation(client=self.factorio_client, result=None, skills_dir=cfg.SKILLS_DIR, production_tracker=self.production_tracker, current_task=task)
        })

        # Agent loop
        while state.status == AgentStatus.RUNNING:
            if state.iteration >= max_iterations:
                state.status = AgentStatus.LIMIT
                break
            state = self._step(state, history_window)
            print(f"executor_agent_run step {state.iteration}")
        return state

    @observe(name="executor_agent_step")
    def _step(self, state: ExecutorState, history_window: int) -> ExecutorState:
        
        state.iteration += 1
        # THINK
        response: Message = call_anthropic(
            client=self.anthropic_client, prompt=executor_prompt.EXECUTOR_PROMPT,
            history=self._windowed_history(state.history, history_window),
        )
        text_block = next((b for b in response.content if isinstance(b, TextBlock)), None)
        if not text_block:
            return state

        parsed: dict[str, Any] = parse_executor_agent_output(text_block.text)
        state.last_action = parsed["action"]

        # VALIDATE - block forbidden patterns before they touch the game
        issues = validate_lua(parsed["action"])
        if has_blocking(issues):
            fake_result = {
                "status": "ERROR",
                "output": "ACTION REJECTED — Lua validator detected forbidden patterns:\n"
                        + format_issues(issues)
                        + "\n\nFix the code so items come from legitimate sources "
                            "(mining, crafting, or inventories you own) and resubmit.",
            }
            observation = build_observation(client=self.factorio_client, result=fake_result, skills_dir=cfg.SKILLS_DIR, production_tracker=self.production_tracker, current_task=state.task)
            state.last_observation = observation
            state.history.append({"role": "assistant", "content": text_block.text})
            state.history.append({"role": "user", "content": observation})
            return state

        # ACT
        if parsed["skill_reused"] == "false":
            result = execute_lua(self.factorio_client, parsed["action"])
            # Save only if it ran cleanly AND has no blocking issues (defensive — should be impossible here)
            if result["status"] == "OK":
                save_skill(parsed["new_skill_name"], parsed["action"])
        elif parsed["skill_reused"] == "true":
            result = reuse_skill(self.factorio_client, parsed["existing_skill_name"])
            record_execution(from_library=True)
        else:
            result = execute_lua(self.factorio_client, parsed["action"])

        # Append non-blocking warnings to the result so the agent sees them
        warns = [i for i in issues if i.severity == "WARN"]
        if warns:
            result["output"] = (result.get("output") or "") + "\n[validator warnings]\n" + format_issues(warns)

        # OBSERVE
        observation = build_observation(client=self.factorio_client, result=result, skills_dir=cfg.SKILLS_DIR, production_tracker=self.production_tracker, current_task=state.task)
        state.last_observation = observation

        state.history.append({"role": "assistant", "content": text_block.text})
        state.history.append({"role": "user", "content": observation})

        if parsed["task_complete"]:
            state.status = AgentStatus.DONE

        return state


class PlannerAgent:
    def __init__(self, anthropic_client, factorio_client, production_tracker, skills_dir):
        self.anthropic_client: anthropic.Anthropic = anthropic_client
        self.factorio_client: factorio_rcon.RCONClient = factorio_client
        self.production_tracker: ProductionTracker = production_tracker
        self.skills_dir: Path = skills_dir

    def _build_planner_context(self, goal: str) -> str:
        entities: list[dict] = get_entity_status(self.factorio_client)
        self.production_tracker.update(entities)
        factory_state: str = format_entity_status(entities)
        metrics: str = self.production_tracker.format_throughput(entities)
        skill_names: list[str] = get_skill_names(self.skills_dir)
        skills_str = "\n".join(f"- {s}" for s in skill_names) or "none"

        return f"""GOAL: {goal}

{factory_state}

{metrics}

=== AVAILABLE SKILLS ===
{skills_str}"""

    @observe(name="planner_agent_create_plan")
    def create_plan(self, task: str) -> PlannerState:
        state = PlannerState(goal=task)
        content = self._build_planner_context(task)
        response = call_anthropic(
            client=self.anthropic_client,
            prompt=planner_prompt.PLANNER_PROMPT,
            history=[{"role": "user", "content": content}],
        )
        text_block = next((b for b in response.content if isinstance(b, TextBlock)), None)
        if not text_block:
            return state
        parsed = parse_planner_agent_output(text_block.text)
        state.reasoning = parsed["reasoning"]
        state.plan = parsed["plan"]
        return state

    @observe(name="planner_agent_replan")
    def replan(self, goal, completed, failed_task, reason) -> PlannerState:
        state = PlannerState(goal=goal)
        context = self._build_planner_context(goal)
        completed_str = "\n".join(f"- {t['description']}" for t in completed) or "None yet"
        failed_str = f"- {failed_task['description']} — reason: {reason}"

        content = f"""ORIGINAL GOAL: {goal}

COMPLETED SUBTASKS:
{completed_str}

FAILED SUBTASK:
{failed_str}

CURRENT STATE:
{context}

Create a new plan to complete the remaining work."""

        response = call_anthropic(
            client=self.anthropic_client,
            prompt=planner_prompt.PLANNER_PROMPT,
            history=[{"role": "user", "content": content}],
        )
        text_block = next((b for b in response.content if isinstance(b, TextBlock)), None)
        if not text_block:
            return state
        parsed = parse_planner_agent_output(text_block.text)
        state.reasoning = parsed["reasoning"]
        state.plan = parsed["plan"]
        return state


class Orchestrator:
    def __init__(
        self,
        planner: PlannerAgent,
        executor: ExecutorAgent,
        max_replans: int,
        max_iterations_per_subtask: int,
        history_window: int,
    ):
        self.planner = planner
        self.executor = executor
        self.max_replans = max_replans
        self.max_iterations_per_subtask = max_iterations_per_subtask
        self.history_window = history_window

    @observe(name="orchestrator_run")
    def run(self, goal: str) -> OrchestratorState:
        state = OrchestratorState(goal=goal)

        # 1. create initial plan
        planner_state: PlannerState = self.planner.create_plan(goal)
        state.plan = planner_state.plan

        # 2. loop through subtasks
        while state.current_subtask_index < len(state.plan):
            subtask = state.plan[state.current_subtask_index]

            # build full task string for executor
            description = subtask["description"]
            criteria = subtask.get("success_criteria", "")
            full_task = f"{description}\n\nSuccess criteria: {criteria}" if criteria else description

            # 3. run executor on subtask
            executor_state = self.executor.run(full_task, self.max_iterations_per_subtask, self.history_window)

            # 4. handle success
            if executor_state.status == AgentStatus.DONE:
                state.completed_subtasks.append(subtask)
                state.current_subtask_index += 1

            # 5. handle failure - replan
            elif executor_state.status in (AgentStatus.LIMIT, AgentStatus.FAILED):
                reason = (
                    f"hit iteration limit after {executor_state.iteration} steps"
                    if executor_state.status == AgentStatus.LIMIT
                    else "executor failed"
                )
                state.failed_subtasks.append((subtask, reason))
                if state.replan_count >= self.max_replans:
                    state.status = AgentStatus.FAILED
                    break

                new_plan_state = self.planner.replan(
                    goal=state.goal,
                    completed=state.completed_subtasks,
                    failed_task=subtask,
                    reason=reason,
                )
                state.plan = new_plan_state.plan
                state.replan_count += 1
                state.current_subtask_index = 0

        # 6. mark complete
        if state.status != AgentStatus.FAILED and state.current_subtask_index >= len(state.plan):
            state.status = AgentStatus.DONE

        return state



if __name__ == "__main__":
    from game_integration.dependencies import get_factorio_client
    from dependencies import get_anthropic_client
    from metrics.production_tracker import ProductionTracker
    import agent.cfg as cfg

    factorio_client = get_factorio_client()
    anthropic_client = get_anthropic_client()
    production_tracker = ProductionTracker()

    planner = PlannerAgent(
        anthropic_client=anthropic_client,
        factorio_client=factorio_client,
        production_tracker=production_tracker,
        skills_dir=cfg.SKILLS_DIR,
    )
    task = "Set up an automated iron production line: mine iron ore with a burner miner, smelt it into iron plates with a furnace, and ensure coal fuels the miner automatically"
    plan_state = planner.create_plan(task)

    print("REASONING:", plan_state.reasoning)
    print()
    # for i, s in enumerate(plan_state.plan, 1):
    #     print(f"{i}. {s['description']}")
    #     print(f"   criteria: {s['success_criteria']}")
    #     print()
    for item in plan_state.plan:
        print(item)


# if __name__ == "__main__":
#     from game_integration.dependencies import get_factorio_client
#     from dependencies import get_anthropic_client
#     from metrics.production_tracker import ProductionTracker
#     import agent.cfg as cfg

#     factorio_client = get_factorio_client()
#     anthropic_client = get_anthropic_client()
#     production_tracker = ProductionTracker()

#     planner = PlannerAgent(
#         anthropic_client=anthropic_client,
#         factorio_client=factorio_client,
#         production_tracker=production_tracker,
#         skills_dir=cfg.SKILLS_DIR,
#     )
#     executor = ExecutorAgent(
#         anthropic_client=anthropic_client,
#         factorio_client=factorio_client,
#         production_tracker=production_tracker,
#         skills_dir=cfg.SKILLS_DIR,
#     )
#     orchestrator = Orchestrator(
#         planner=planner,
#         executor=executor,
#         max_replans=2,
#         max_iterations_per_subtask=20,
#         history_window=3,
#     )

#     task = "Set up an automated iron production line: mine iron ore with a burner miner, smelt it into iron plates with a furnace, and ensure coal fuels the miner automatically"
#     result = orchestrator.run(task)
#     print("STATUS:", result.status)
#     print("COMPLETED:", [s["description"] for s in result.completed_subtasks])