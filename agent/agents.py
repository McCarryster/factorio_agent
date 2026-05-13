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
from game_integration.lua_validator import validate_lua, has_blocking, format_issues
from game_integration.factorio_bridge import execute_lua
from observability.build_observation import build_observation, get_world_state
from metrics.reward_t import RewardCalculator
# from metrics.reward_diagnose import DiagnosticRewardCalculator as RewardCalculator



# ============================================================
# Dataclasses
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
    """State for one planning call."""
    goal: str = "" # the overall user goal
    plan: list[str] = field(default_factory=list)
    reasoning: str = ""

@dataclass
class OrchestratorState:
    """State across the whole multi-agent run."""
    goal: str
    plan: list[str] = field(default_factory=list)
    completed_subtasks: list[str] = field(default_factory=list)
    failed_subtasks: list[tuple[str, str]] = field(default_factory=list)  # (task, reason)
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
                 reward_calc: RewardCalculator,
                 skills_dir: Path):
        self.anthropic_client: anthropic.Anthropic = anthropic_client
        self.factorio_client: factorio_rcon.RCONClient = factorio_client
        self.reward_calc: RewardCalculator = reward_calc
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
        self.reward_calc.reset()
        state.history.append({
            "role": "user",
            "content": f"TASK: {task}" + build_observation(
                client=self.factorio_client, reward_calc=self.reward_calc,
                result=None, skills_dir=cfg.SKILLS_DIR, start=True,
            ),
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
            observation = build_observation(
                client=self.factorio_client, reward_calc=self.reward_calc,
                result=fake_result, skills_dir=self.skills_dir, start=False,
            )
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
        observation = build_observation(
            client=self.factorio_client, reward_calc=self.reward_calc,
            result=result, skills_dir=self.skills_dir, start=False,
        )
        state.last_observation = observation

        state.history.append({"role": "assistant", "content": text_block.text})
        state.history.append({"role": "user", "content": observation})

        if parsed["task_complete"]:
            state.status = AgentStatus.DONE

        return state


class PlannerAgent:
    def __init__(self, 
                 anthropic_client: anthropic.Anthropic,
                 factorio_client: factorio_rcon.RCONClient,
                 skills_dir: Path):
        self.anthropic_client: anthropic.Anthropic = anthropic_client
        self.factorio_client: factorio_rcon.RCONClient = factorio_client
        self.skills_dir: Path = skills_dir

    @observe(name="planner_agent_create_plan")
    def create_plan(self, task: str) -> PlannerState:
        state = PlannerState(goal=task)
        world_state: str = get_world_state(client=self.factorio_client, radius=64, skills_dir=self.skills_dir)
        content = f"GOAL: {task}\n\n{world_state}"

        response: Message = call_anthropic(
            client=self.anthropic_client, prompt=planner_prompt.PLANNER_PROMPT,
            history=[{"role": "user", "content": content}],
        )
        text_block = next((b for b in response.content if isinstance(b, TextBlock)), None)
        if not text_block:
            return state

        parsed: dict[str, Any] = parse_planner_agent_output(text_block.text)
        state.reasoning = parsed['reasoning']
        state.plan = parsed['plan']

        return state

    @observe(name="planner_agent_replan")
    def replan(
        self,
        goal: str,
        completed: list[str],
        failed_task: str,
        reason: str,
    ) -> PlannerState:
        state = PlannerState(goal=goal)
        
        world_state: str = get_world_state(client=self.factorio_client, radius=64, skills_dir=self.skills_dir)
        
        completed_str = "\n".join(f"Done {t}" for t in completed) or "None yet"
        failed_str = f"FAIL {failed_task} - {reason}"

        content = f"""ORIGINAL GOAL: {goal}

        COMPLETED SUBTASKS:
        {completed_str}

        FAILED SUBTASK:
        {failed_str}

        CURRENT WORLD STATE:
        {world_state}

        Create a new plan to complete the remaining work."""

        response: Message = call_anthropic(
            client=self.anthropic_client, prompt=planner_prompt.PLANNER_PROMPT,
            history=[{"role": "user", "content": content}],
        )
        text_block = next((b for b in response.content if isinstance(b, TextBlock)), None)
        if not text_block:
            return state

        parsed: dict[str, Any] = parse_planner_agent_output(text_block.text)
        state.reasoning = parsed['reasoning']
        state.plan = parsed['plan']
        
        return state


# class VerifierAgent:
#     def __init__(self, anthropic_client, factorio_client, skills_dir):
#         self.anthropic_client = anthropic_client
#         self.factorio_client = factorio_client
#         self.skills_dir = skills_dir

#     def verify(self, subtask: str) -> VerifierState:
#         state = VerifierState(subtask=subtask)
#         world_state = get_world_state(client=self.factorio_client, radius=64, skills_dir=self.skills_dir)
        
#         content = f"""SUBTASK: {subtask}
        
# CURRENT WORLD STATE:
# {world_state}

# Was this subtask actually completed? Check the world state carefully.
# """
#         response = call_anthropic(
#             client=self.anthropic_client,
#             prompt=VERIFIER_PROMPT,
#             history=[{"role": "user", "content": content}]
#         )
#         # parse verified and reason
#         ...
#         return state


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
            # 3. run executor on subtask
            executor_state = self.executor.run(subtask, self.max_iterations_per_subtask, self.history_window)

            # 4. handle success
            if executor_state.status == AgentStatus.DONE:
                state.completed_subtasks.append(subtask)
                state.current_subtask_index += 1

            # 5. handle failure - replan
            elif executor_state.status in (AgentStatus.LIMIT, AgentStatus.FAILED):
                reason = f"hit iteration limit after {executor_state.iteration} steps" \
                        if executor_state.status == AgentStatus.LIMIT \
                        else "executor failed"
                state.failed_subtasks.append((subtask, reason))
                if state.replan_count >= self.max_replans:
                    state.status = AgentStatus.FAILED
                    break

                # replan
                new_plan_state = self.planner.replan(
                        goal=state.goal,
                        completed=state.completed_subtasks,
                        failed_task=subtask,
                        reason=reason
                    )
                state.plan = new_plan_state.plan
                state.replan_count += 1
                state.current_subtask_index = 0 # don't increment current_subtask_index - replan replaces remaining plan

        # 6. mark complete if all subtasks done
        if state.status != AgentStatus.FAILED and state.current_subtask_index >= len(state.plan):
            state.status = AgentStatus.DONE

        return state


if __name__ == "__main__":
    from agent.dependencies import get_anthropic_client
    from game_integration.dependencies import get_factorio_client

    anthropic_client = get_anthropic_client()
    factorio_client = get_factorio_client()
    reward_calc = RewardCalculator(factorio_client)
    
    executor = ExecutorAgent(anthropic_client, factorio_client, reward_calc, cfg.SKILLS_DIR)
    planner = PlannerAgent(anthropic_client, factorio_client, cfg.SKILLS_DIR)
    orchestrator = Orchestrator(
        planner=planner,
        executor=executor,
        max_replans=3,
        max_iterations_per_subtask=10,
        history_window=8,
    )

    task = "Set up an automated iron production line: mine iron ore with a burner miner, smelt it into iron plates with a furnace, and ensure coal fuels the miner automatically, store iron plates to chest"
    # task = "Build a fully automated iron plate production line"
    result = orchestrator.run(task)

    print(f"Status: {result.status}")
    print(f"Completed: {result.completed_subtasks}")
    print(f"Failed: {result.failed_subtasks}")