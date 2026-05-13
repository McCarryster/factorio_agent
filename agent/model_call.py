"""
model_calls.py — LLM API call with Langfuse generation tracing.
"""

import anthropic
from anthropic import APIStatusError
from anthropic.types import MessageParam, Message, TextBlock
from langfuse import get_client, observe
import agent.cfg as cfg
import time


langfuse = get_client()

@observe(name="claude_response", as_type="generation")
def call(
    client: anthropic.Anthropic,
    prompt: str,
    history: list[MessageParam],
) -> Message:
    """
    Call the Anthropic Messages API and record the generation in Langfuse.

    Args:
        client: Anthropic client instance.
        model: Model ID string.
        max_tokens: Maximum tokens to generate.
        prompt: System prompt string.
        history: Conversation history.

    Returns:
        The raw Anthropic Message response.
    """
    langfuse.update_current_generation(model=cfg.DEFAULT_MODEL, input=[{"role": "system", "content": prompt}] + history)

    result = client.messages.create(
        model=cfg.DEFAULT_MODEL,
        max_tokens=cfg.MAX_TOKENS,
        system=prompt,
        messages=history,
    )

    text_block = next((b for b in result.content if isinstance(b, TextBlock)), None)
    langfuse.update_current_generation(
        output=text_block.text if text_block else "",
        usage_details={
            "input": result.usage.input_tokens,
            "output": result.usage.output_tokens,
        },
    )

    return result


def call_anthropic(
    client: anthropic.Anthropic,
    prompt: str,
    history: list[MessageParam],
    max_retries: int = 3,
) -> Message:
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            return call(client, prompt, history)
        except APIStatusError as e:
            if e.status_code == 529:
                if attempt == max_retries - 1:
                    raise
                wait = 2 ** attempt
                print(f"API overloaded, retrying in {wait}s...")
                time.sleep(wait)
                last_error = e
            else:
                raise
    raise last_error  # type: ignore