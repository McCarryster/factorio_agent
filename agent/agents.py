"""
TODO: description
"""


import factorio_rcon

import anthropic
from anthropic.types import MessageParam, Message, TextBlock

from enum import Enum
from dataclasses import dataclass, field
from pathlib import Path

from langfuse import observe

import agent.cfg as cfg
from agent.prompt import executor_prompt
from agent.model_call import call_anthropic
from agent.utils import parse_agent_output, save_skill, reuse_skill
from metrics.skill_reuse import record_execution
from game_integration.lua_validator import validate_lua, has_blocking, format_issues
from game_integration.factorio_bridge import execute_lua
from metrics.reward_t import RewardCalculator
from observability.build_observation import build_observation
# from metrics.reward_diagnose import DiagnosticRewardCalculator as RewardCalculator



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


class Executor:
    def __init__(self, anthropic_client: anthropic.Anthropic,
                 factorio_client: factorio_rcon.RCONClient,
                 reward_calc: RewardCalculator,
                 skills_dir: Path):
        self.anthropic_client: anthropic.Anthropic = anthropic_client
        self.factorio_client: factorio_rcon.RCONClient = factorio_client
        self.reward_calc: RewardCalculator = reward_calc
        self.skills_dir: Path = skills_dir
        self.state: ExecutorState = ExecutorState()

    def _windowed_history(self, history: list, window: int | None) -> list:
        """Return [first_msg] + last `window` messages, or full history if window is None."""
        if window is None or len(history) <= window + 1:
            return history
        return [history[0]] + history[-window:]

    @observe(name="executor_agent_run")
    def run(self, task: str, max_iterations: int, history_window: int) -> ExecutorState:
        self.state = ExecutorState(task=task)
        self.reward_calc.reset()
        self.state.history.append({
            "role": "user",
            "content": f"TASK: {task}" + build_observation(
                client=self.factorio_client, reward_calc=self.reward_calc,
                result=None, skills_dir=cfg.SKILLS_DIR, start=True,
            ),
        })

        while self.state.status == AgentStatus.RUNNING:
            if self.state.iteration >= max_iterations:
                self.state.status = AgentStatus.LIMIT
                break
            state = self._step(self.state, history_window)
            print(f"step = {state.iteration}")
        return self.state

    @observe(name="executor_agent_step")
    def _step(self, history_window: int) -> ExecutorState:
        
        self.state.iteration += 1
        # THINK
        response: Message = call_anthropic(
            client=self.anthropic_client, prompt=executor_prompt.EXECUTOR_PROMPT,
            history=self._windowed_history(self.state.history, history_window),
        )
        text_block = next((b for b in response.content if isinstance(b, TextBlock)), None)
        if not text_block:
            return self.state

        parsed = parse_agent_output(text_block.text)
        self.state.last_action = parsed["action"]

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
        self.state.last_observation = observation

        self.state.history.append({"role": "assistant", "content": text_block.text})
        self.state.history.append({"role": "user", "content": observation})

        if parsed["task_complete"]:
            self.state.status = AgentStatus.DONE

        return self.state


class Planner:
    def __init__(self, anthropic_client: anthropic.Anthropic):
        self.anthropic_client: anthropic.Anthropic = anthropic_client
        self.state: PlannerState = PlannerState()

    def create_plan(self, goal: str, world_state: dict) -> list[str]:
        ...
    def replan(self, ...) -> list[str]:
        ...


class Orchestrator:
    def __init__(self, planner: Planner, executor: Executor):
        self.planner: Planner = planner
        self.executor: Executor = executor

    def run(self, goal: str):
        ...