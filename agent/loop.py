"""
loop.py — Main agent loop with Langfuse tracing.

Trace hierarchy per run:
    factorio-agent (trace)
    └── agent-step (span, repeated)
        └── claude-response (generation)
"""

from anthropic.types import Message
import anthropic
import factorio_rcon

from langfuse import observe

from agent.prompt import PROMPT
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


@observe(name="factorio-agent")
def run(task: str, max_iterations: int = 20) -> AgentState:
    state = AgentState(task=task)
    state.history.append({
        "role": "user",
        "content": f"TASK: {task}" + build_observation(factorio_client, result=None, radius=64, start=True),
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
        max_tokens=1024, prompt=PROMPT, history=state.history,
    )
    parsed = parse_agent_output(response.content[0].text)  # type: ignore[union-attr]
    state.last_action = parsed["action"]

    # ACT
    build_observation(factorio_client, result=None, radius=64, start=True) # takes inventory snapshot for proper reward calculation
    result = execute_lua(factorio_client, parsed["action"])

    # OBSERVE
    observation = build_observation(factorio_client, result=result, radius=64, start=False)
    state.last_observation = observation

    # UPDATE HISTORY
    state.history.append({"role": "assistant", "content": response.content[0].text})  # type: ignore[union-attr]
    state.history.append({"role": "user",      "content": observation})

    if parsed["done"]:
        state.status = AgentStatus.DONE

    return state



if __name__ == "__main__":
    run("Place the burner-mining-drill on an iron ore patch and the stone-furnace next to it. Add coal to both.")