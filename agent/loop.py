"""
loop.py — Main agent loop with Langfuse tracing.

Trace hierarchy per run:
    factorio-agent (trace)
    └── agent-step (span, repeated)
        └── claude-response (generation)
"""


from anthropic.types import Message, TextBlock
import anthropic
import factorio_rcon
from langfuse import observe
from agent.prompt.prompt import SYSTEM_PROMPT
from agent.utils import parse_agent_output, save_skill, reuse_skill
from agent.state import AgentState, AgentStatus
from agent.dependencies import get_anthropic_client
from agent.model_calls import call_anthropic
from agent.observability.build_observation import build_observation
import agent.cfg as cfg
from game_integration.factorio_bridge import execute_lua
from game_integration.dependencies import get_client
from game_integration.lua_validator import validate_lua, has_blocking, format_issues
from metrics.skill_reuse import record_execution
from metrics.reward_t import RewardCalculator
# from metrics.reward_diagnose import DiagnosticRewardCalculator as RewardCalculator


client: anthropic.Anthropic = get_anthropic_client()
factorio_client: factorio_rcon.RCONClient = get_client()
reward_calc: RewardCalculator = RewardCalculator(factorio_client)


def _windowed_history(
    history: list, window: int | None
) -> list:
    """Return [first_msg] + last `window` messages, or full history if window is None."""
    if window is None or len(history) <= window + 1:
        return history
    return [history[0]] + history[-window:]


@observe(name="factorio-agent")
def run(task: str, max_iterations: int = 100, history_window: int | None = None) -> AgentState:
    """Run the agent loop for a given task."""
    state = AgentState(task=task, history_window=history_window)
    # Establish reward baseline at episode start. The first observation
    # below will produce reward=0 (or close to it) by design.
    reward_calc.reset()
    state.history.append({
        "role": "user",
        "content": f"TASK: {task}" + build_observation(
            client=factorio_client, reward_calc=reward_calc,
            result=None, skills_dir=cfg.SKILLS_DIR, start=True,
        ),
    })

    while not state.is_terminal:
        if state.iteration >= max_iterations:
            state.status = AgentStatus.LIMIT
            break
        state = _step(state)
        print(f"step = {state.iteration}")

    return state


@observe(name="agent-step")
def _step(state: AgentState) -> AgentState:
    state.iteration += 1

    # THINK
    response: Message = call_anthropic(
        client=client, model=cfg.DEFAULT_MODEL,
        max_tokens=cfg.MAX_TOKENS, prompt=SYSTEM_PROMPT,
        history=_windowed_history(state.history, state.history_window),
    )
    text_block = next((b for b in response.content if isinstance(b, TextBlock)), None)
    if not text_block:
        return state

    parsed = parse_agent_output(text_block.text)
    state.last_action = parsed["action"]

    # VALIDATE — block forbidden patterns before they touch the game
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
            client=factorio_client, reward_calc=reward_calc,
            result=fake_result, skills_dir=cfg.SKILLS_DIR, start=False,
        )
        state.last_observation = observation
        state.history.append({"role": "assistant", "content": text_block.text})
        state.history.append({"role": "user", "content": observation})
        return state

    # ACT
    if parsed["skill_reused"] == "false":
        result = execute_lua(factorio_client, parsed["action"])
        # Save only if it ran cleanly AND has no blocking issues (defensive — should be impossible here)
        if result["status"] == "OK":
            save_skill(parsed["new_skill_name"], parsed["action"])
    elif parsed["skill_reused"] == "true":
        result = reuse_skill(factorio_client, parsed["existing_skill_name"])
        record_execution(from_library=True)
    else:
        result = execute_lua(factorio_client, parsed["action"])

    # Append non-blocking warnings to the result so the agent sees them
    warns = [i for i in issues if i.severity == "WARN"]
    if warns:
        result["output"] = (result.get("output") or "") + "\n[validator warnings]\n" + format_issues(warns)

    # OBSERVE
    observation = build_observation(
        client=factorio_client, reward_calc=reward_calc,
        result=result, skills_dir=cfg.SKILLS_DIR, start=False,
    )
    state.last_observation = observation

    state.history.append({"role": "assistant", "content": text_block.text})
    state.history.append({"role": "user", "content": observation})

    if parsed["done"]:
        state.status = AgentStatus.DONE

    return state


if __name__ == "__main__":
    # task = "Place the burner-mining-drill on an iron ore patch and the stone-furnace next to it. Add fuel to both."
    task = "Set up an automated iron production line: mine iron ore with a burner miner, smelt it into iron plates with a furnace, and ensure coal fuels the miner automatically"
    max_iterations = 50
    history_window = 8
    run(task, max_iterations, history_window)