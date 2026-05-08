"""
loop.py — Main agent loop with Langfuse tracing.

Trace hierarchy per run:
    factorio-agent (trace)
    └── agent-step (span, repeated)
        └── claude-response (generation)
"""

import re

from anthropic.types import Message, TextBlock
import anthropic
import factorio_rcon

from langfuse import observe

from agent.prompt import SYSTEM_PROMPT, SKILL_SAVE_PROMPT
from agent.utils import parse_agent_output
from agent.state import AgentState, AgentStatus
from agent.dependencies import get_anthropic_client
from agent.model_calls import call_anthropic
from agent.observability.build_observation import build_observation
import agent.cfg as cfg
from game_integration.factorio_bridge import execute_lua
from game_integration.dependencies import get_client

client: anthropic.Anthropic = get_anthropic_client()
factorio_client: factorio_rcon.RCONClient = get_client()


def _windowed_history(
    history: list, window: int | None
) -> list:
    """Return [first_msg] + last `window` messages, or full history if window is None."""
    if window is None or len(history) <= window + 1:
        return history
    return [history[0]] + history[-window:]


@observe(name="skill-save")
def maybe_save_skill(lua_code: str, result: dict[str, str]) -> None:
    """Ask Claude whether the executed Lua should be saved as a reusable skill."""
    if result["status"] == "ERROR":
        return

    prompt = SKILL_SAVE_PROMPT.replace("<code>", f"\n```lua\n{lua_code}\n```\n")
    response: Message = client.messages.create(
        model=cfg.DEFAULT_MODEL,
        max_tokens=128,
        messages=[{"role": "user", "content": prompt}],
    )

    text_block = next((b for b in response.content if isinstance(b, TextBlock)), None)
    if not text_block:
        return

    text = text_block.text
    name_match = re.search(r"<skill_name>(.*?)</skill_name>", text, re.DOTALL)
    desc_match = re.search(r"<skill_description>(.*?)</skill_description>", text, re.DOTALL)

    if not name_match:
        return

    skill_name = name_match.group(1).strip()
    skill_desc = desc_match.group(1).strip() if desc_match else ""

    cfg.SKILLS_DIR.mkdir(exist_ok=True)
    (cfg.SKILLS_DIR / f"{skill_name}.lua").write_text(
        f"-- {skill_desc}\n{lua_code}", encoding="utf-8"
    )
    print(f"skill saved: {skill_name}")


@observe(name="factorio-agent")
def run(task: str, max_iterations: int = 20, history_window: int | None = None) -> AgentState:
    """Run the agent loop for a given task."""
    state = AgentState(task=task, history_window=history_window)
    state.history.append({
        "role": "user",
        "content": f"TASK: {task}" + build_observation(factorio_client, result=None, radius=64, skills_dir=cfg.SKILLS_DIR, start=True),
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
        max_tokens=1024, prompt=SYSTEM_PROMPT,
        history=_windowed_history(state.history, state.history_window),
    )
    text_block = next((b for b in response.content if isinstance(b, TextBlock)), None)
    if not text_block:
        return state

    parsed = parse_agent_output(text_block.text)
    state.last_action = parsed["action"]

    # ACT — pre-snapshot so reward delta is calculated correctly
    build_observation(factorio_client, result=None, radius=64, skills_dir=cfg.SKILLS_DIR, start=True)
    result = execute_lua(factorio_client, parsed["action"])
    maybe_save_skill(parsed["action"], result)

    # OBSERVE
    observation = build_observation(factorio_client, result=result, radius=64, skills_dir=cfg.SKILLS_DIR, start=False)
    state.last_observation = observation

    # UPDATE HISTORY
    state.history.append({"role": "assistant", "content": text_block.text})
    state.history.append({"role": "user",      "content": observation})

    if parsed["done"]:
        state.status = AgentStatus.DONE

    return state


if __name__ == "__main__":
    task = "Place the burner-mining-drill on an iron ore patch and the stone-furnace next to it. Add coal to both."
    max_iterations = 20
    history_window = 8
    run(task, max_iterations, history_window)