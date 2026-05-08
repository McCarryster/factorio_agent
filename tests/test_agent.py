"""
test_agent.py — Integration-style tests for the agent loop.

Set REAL_CALLS = True to use the real Anthropic API (costs money + needs keys).
Set REAL_CALLS = False to use dummy responses and validate tracing / flow only.
Factorio RCON is always mocked — no live game required.
"""

import re
from unittest.mock import patch, MagicMock

import pytest
from anthropic.types import Message, TextBlock, Usage
from langfuse import observe

# ── CONFIGURATION ─────────────────────────────────────────────────────────────

REAL_CALLS: bool = False  # flip to True to hit the real Claude API

# ── MODULE IMPORT (patch RCON side-effect before import) ──────────────────────

_mock_rcon = MagicMock()

with patch("game_integration.dependencies.get_client", return_value=_mock_rcon):
    with patch("agent.dependencies.get_anthropic_client", return_value=MagicMock()):
        import agent.loop as loop

# ── DUMMY RESPONSE HELPERS ────────────────────────────────────────────────────

def _make_message(thought: str, action: str, done: bool) -> Message:
    text = (
        f"<thought>{thought}</thought>"
        f"<action>{action}</action>"
        f"<done>{str(done).lower()}</done>"
    )
    return Message(
        id="msg_test",
        type="message",
        role="assistant",
        content=[TextBlock(type="text", text=text)],
        model="claude-haiku-4-5-20251001",
        stop_reason="end_turn",
        stop_sequence=None,
        usage=Usage(input_tokens=10, output_tokens=20),
    )


_DUMMY_STEPS: list[Message] = [
    _make_message(
        thought="I should check what resources are nearby before doing anything.",
        action="local pos = game.players[1].position\nrcon.print(serpent.block(pos))",
        done=False,
    ),
    _make_message(
        thought="Good. Now I'll place a burner-mining-drill on the iron ore patch.",
        action="game.players[1].surface.create_entity{name='burner-mining-drill', position={0,0}, force='player'}",
        done=False,
    ),
    _make_message(
        thought="Drill is placed. Task complete.",
        action="rcon.print('done')",
        done=True,
    ),
]


def _dummy_caller(steps: list[Message]):
    """Returns a call_anthropic replacement that cycles through steps."""
    it = iter(steps)
    last = steps[-1]

    @observe(name="claude-response", as_type="generation")
    def _call(**_) -> Message:
        return next(it, last)

    return _call


# ── PRETTY PRINTER ────────────────────────────────────────────────────────────

_W = 88


def _extract(tag: str, text: str) -> str | None:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    return m.group(1).strip() if m else None


def _print_block(label: str, content: str) -> None:
    print(f"|  [{label}]")
    for line in content.splitlines():
        print(f"|    {line}")


def _print_history(history: list[dict]) -> None:
    print(f"\n{'=' * _W}")
    for i, msg in enumerate(history):
        role = msg["role"].upper()
        content = str(msg["content"])
        print(f"+{'-' * (_W - 1)}")
        if role == "ASSISTANT":
            thought = _extract("thought", content)
            action = _extract("action", content)
            done = _extract("done", content)
            print(f"|  [{i}] ASSISTANT")
            if thought:
                _print_block("THOUGHT", thought)
            if action:
                _print_block("ACTION", action)
            if done is not None:
                print(f"|  [DONE] {done}")
        else:
            print(f"|  [{i}] {role}")
            for line in content.splitlines():
                print(f"|    {line}")
        print(f"+{'-' * (_W - 1)}")
    print(f"{'=' * _W}\n")


# ── FIXTURES ──────────────────────────────────────────────────────────────────

FAKE_OBSERVATION = (
    "INVENTORY: iron-ore: 5.\n"
    "POSITION: (0, 0).\n"
    "NEARBY CLUSTERS OF RESOURCES: iron-ore at (2, 3) amount=500, tiles=12.\n"
    "METRICS:\n"
    "    reward this step: 0.0\n"
    "    unique items seen: 1\n"
    "    entities placed: 0\n"
    "    technologies researched: 0\n"
    "    technology tree depth: 0\n"
    "    skills in library: 0\n"
    "    skill reuse rate: 0.0"
)

FAKE_RESULT = {"status": "OK", "output": "executed successfully"}


@pytest.fixture
def patched_loop(monkeypatch: pytest.MonkeyPatch):
    """Patches RCON, build_observation, and execute_lua. Optionally patches call_anthropic."""
    def _fake_observation(*_args, **_kwargs) -> str:
        return FAKE_OBSERVATION

    def _fake_execute(*_args, **_kwargs) -> dict:
        return FAKE_RESULT

    monkeypatch.setattr(loop, "factorio_client", MagicMock())
    monkeypatch.setattr(loop, "build_observation", _fake_observation)
    monkeypatch.setattr(loop, "execute_lua", _fake_execute)

    if not REAL_CALLS:
        monkeypatch.setattr(loop, "call_anthropic", _dummy_caller(_DUMMY_STEPS))
    else:
        from agent.dependencies import get_anthropic_client
        monkeypatch.setattr(loop, "client", get_anthropic_client())

    return loop


# ── TESTS ─────────────────────────────────────────────────────────────────────

def test_agent_run_completes(patched_loop: object) -> None:
    """Agent runs to DONE or LIMIT and produces a non-empty history."""
    state = patched_loop.run(  # type: ignore[attr-defined]
        "Place a burner-mining-drill on an iron ore patch.",
        max_iterations=len(_DUMMY_STEPS) + 1,
    )

    _print_history(state.history)

    assert state.iteration > 0
    assert state.status.name in ("DONE", "LIMIT")
    assert len(state.history) >= 2  # at least initial user msg + one assistant turn


def test_agent_hits_limit(patched_loop: object) -> None:
    """Agent stops at max_iterations when task is never marked done."""
    from agent.state import AgentStatus

    # Override call_anthropic to never return done=True
    never_done = _make_message("thinking...", "rcon.print('x')", done=False)
    patched_loop.call_anthropic = _dummy_caller([never_done] * 10)  # type: ignore[attr-defined]

    state = patched_loop.run("Run forever.", max_iterations=3)  # type: ignore[attr-defined]

    assert state.status == AgentStatus.LIMIT
    assert state.iteration == 3
