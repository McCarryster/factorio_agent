"""
Executor agent that translates one atomic planner task into
a sequence of primitive calls, executes them, and returns
a structured result.

Input:
    - atomic task + context from planner
    - primitive API reference
    - execution budget

Output:
    - ExecutorResult with actions taken, errors, success flag
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional

import anthropic
from anthropic.types import MessageParam, Message, TextBlock
from anthropic.types import TextBlock
import factorio_rcon

from agent.model_call import call_anthropic
from game_integration.primitives import (
    validate_actions,
    translate_to_lua,
    parse_execution_output,
    ExecutionReport,
    EXECUTOR_OUTPUT_SCHEMA,
)
from game_integration.factorio_bridge import execute_lua


# ---------------------------------------------------------------------------
# Executor system prompt
# ---------------------------------------------------------------------------

EXECUTOR_SYSTEM_PROMPT = """\
You are the Executor for a Factorio automation agent.

You receive one atomic task with exact context — positions, entity names, \
what needs to happen — and translate it into a sequence of primitive actions.

== YOUR JOB ==
Read the task and context carefully.
Output the minimum sequence of primitive actions to complete the task.
Use ONLY the coordinates and entity names given in the context.
Do NOT invent positions. Do NOT guess entity names.

== PRIMITIVE API ==
{executor_output_schema}

== RULES ==
1. Use coordinates EXACTLY as given in context. Do not adjust or round them.
2. Entity names must be exact Factorio internal names.
   Examples: "small-electric-pole", "stone-furnace", "burner-mining-drill",
             "inserter", "transport-belt", "wooden-chest"
3. direction must be one of: NORTH, EAST, SOUTH, WEST
4. Keep the sequence SHORT. Only include actions needed for this task.
5. Do not add verification steps — the verifier handles that separately.
6. If the task says "place a pole at (X, Y)", place it at exactly (X, Y).
7. Respond with JSON only. No text outside the JSON object.

== BUDGET ==
Maximum actions: {max_actions}
Stay within budget. Prefer fewer, correct actions over many speculative ones.
"""


# ---------------------------------------------------------------------------
# Executor result
# ---------------------------------------------------------------------------

@dataclass
class ExecutorResult:
    """
    Full result from one executor run.
    Stored in episodic memory and shown to the verifier.
    """
    task: str
    success: bool
    reasoning: list[str]
    report: Optional[ExecutionReport]

    # Raw outputs for debugging
    raw_llm_output: str = ""
    parse_error: str    = ""

    def summary(self) -> str:
        lines = []
        lines.append(f"task: {self.task}")
        lines.append(f"success: {self.success}")
        if self.reasoning:
            lines.append("reasoning:")
            for r in self.reasoning:
                lines.append(f"  - {r}")
        if self.report:
            lines.append(self.report.summary())
        if self.parse_error:
            lines.append(f"parse_error: {self.parse_error}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Executor agent
# ---------------------------------------------------------------------------

class ExecutorAgent:
    def __init__(
        self,
        anthropic_client: anthropic.Anthropic,
        factorio_client: factorio_rcon.RCONClient,
        max_actions: int = 20,
        max_retries: int = 2,
    ):
        self.anthropic_client = anthropic_client
        self.factorio_client  = factorio_client
        self.max_actions      = max_actions
        self.max_retries      = max_retries

    def _build_system_prompt(self) -> str:
        return EXECUTOR_SYSTEM_PROMPT.format(
            executor_output_schema=EXECUTOR_OUTPUT_SCHEMA,
            max_actions=self.max_actions,
        )

    def _build_user_message(self, task: str, context: str) -> str:
        return f"""\
=== TASK ===
{task}

=== CONTEXT ===
{context}

Translate this task into the minimum sequence of primitive actions.
Respond with JSON only.
"""

    def _parse_llm_output(self, raw: str) -> tuple[list[str], list[dict], str]:
        """
        Parse LLM output into (reasoning, actions, error).
        Returns error string if parsing fails.
        """
        # Strip markdown fences
        text = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        text = re.sub(r"\s*```$", "", text)

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            return [], [], f"JSON parse error: {e}\nRaw: {raw[:200]}"

        reasoning = data.get("reasoning", [])
        actions   = data.get("actions", [])

        if not isinstance(actions, list):
            return reasoning, [], "actions field must be a list"

        return reasoning, actions, ""

    def run(self, task: str, context: str) -> ExecutorResult:
        """
        Translate task+context into primitive calls and execute them.

        Retries up to max_retries times if the LLM output fails validation.
        Does NOT retry on game execution errors — those go back to the planner.
        """
        system  = self._build_system_prompt()
        message = self._build_user_message(task, context)
        history: list[MessageParam] = [{"role": "user", "content": message}]

        reasoning: list[str] = []
        actions:   list[dict] = []
        parse_error = ""
        raw_output  = ""

        # Retry loop — only for LLM output parse/validation failures
        for attempt in range(self.max_retries + 1):
            response = call_anthropic(
                client=self.anthropic_client,
                prompt=system,
                history=history,
            )

            text_block = next(
                (b for b in response.content if isinstance(b, TextBlock)), None
            )
            if not text_block:
                parse_error = "LLM returned no text content"
                continue

            raw_output = text_block.text
            reasoning, actions, parse_error = self._parse_llm_output(raw_output)

            if parse_error:
                # Feed the error back so the LLM can fix its output
                history.append({"role": "assistant", "content": raw_output})
                history.append({"role": "user", "content":
                    f"Your output could not be parsed: {parse_error}\n"
                    f"Please respond with valid JSON only."
                })
                continue

            # Validate action schema
            validation_errors = validate_actions(actions)
            if validation_errors:
                error_str = "\n".join(str(e) for e in validation_errors)
                parse_error = f"Validation errors:\n{error_str}"
                history.append({"role": "assistant", "content": raw_output})
                history.append({"role": "user", "content":
                    f"Your actions failed validation:\n{error_str}\n"
                    f"Fix the actions and respond with valid JSON only."
                })
                continue

            # Passed — break out of retry loop
            parse_error = ""
            break

        # If we still have a parse/validation error after all retries
        if parse_error:
            return ExecutorResult(
                task=task,
                success=False,
                reasoning=reasoning,
                report=None,
                raw_llm_output=raw_output,
                parse_error=parse_error,
            )

        # Translate to Lua and execute
        lua = translate_to_lua(actions, max_actions=self.max_actions)
        result = execute_lua(self.factorio_client, lua)

        if result["status"] == "ERROR":
            return ExecutorResult(
                task=task,
                success=False,
                reasoning=reasoning,
                report=None,
                raw_llm_output=raw_output,
                parse_error=f"Lua execution error: {result['output']}",
            )

        report = parse_execution_output(result["output"])

        return ExecutorResult(
            task=task,
            success=report.success,
            reasoning=reasoning,
            report=report,
            raw_llm_output=raw_output,
        )


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from game_integration.dependencies import get_factorio_client
#     from agent.dependencies import get_anthropic_client
#     from game_integration.factorio_bridge import execute_lua as _execute_lua

    factorio_client  = get_factorio_client()
#     anthropic_client = get_anthropic_client()

#     # Give player some poles to work with
#     # _execute_lua(factorio_client, 'game.players[1].insert{name="small-electric-pole", count=5}')

#     executor = ExecutorAgent(
#         anthropic_client=anthropic_client,
#         factorio_client=factorio_client,
#         max_actions=10,
#     )

#     # Task matching the broken factory scenario
#     task = "RESTORE_POWER_TO_SMELTING"

#     context = """\
# The power network ends at pole@(39.5,27.5).
# The nearest unpowered consumer is inserter@(41.5,27.5).
# Gap: 12.5 tiles. Supply radius: 7.5 tiles. Shortfall: 5.0 tiles.
# Place 2 small-electric-poles to bridge the gap:
#   - first pole at (41.5, 27.5) — this reaches the unpowered inserters
#   - second pole at (44.5, 27.5) — this extends coverage further east
# Player has: small-electric-pole x5
# """

#     print("=== EXECUTOR TEST ===\n")
#     print(f"Task: {task}")
#     print(f"Context:\n{context}")
#     print("\nCalling executor...\n")

#     result = executor.run(task, context)
#     print(result.summary())

    from game_integration.factorio_bridge import execute_lua

    lua = """
    local surface = game.surfaces['nauvis']
    local positions = {
        {41.5, 27.5}, {42.5, 27.5}, {43.5, 27.5}, {44.5, 27.5},
        {40.5, 27.5}, {41.5, 26.5}, {42.5, 26.5}, {43.5, 26.5},
    }
    for _, pos in ipairs(positions) do
        local ents = surface.find_entities_filtered{position=pos, radius=0.8}
        local names = {}
        for _, e in ipairs(ents) do
            if e.type ~= "character" then
                table.insert(names, e.name)
            end
        end
        local can = surface.can_place_entity{
            name="small-electric-pole", position=pos, force="player"
        }
        rcon.print(pos[1] .. "," .. pos[2] ..
            " | occupied:" .. table.concat(names, ",") ..
            " | can_place:" .. tostring(can))
    end
    """
    result = execute_lua(factorio_client, lua)
    print(result["output"])