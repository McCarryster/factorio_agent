"""
model_calls.py — LLM API call with Langfuse generation tracing.
"""

import anthropic
from anthropic.types import MessageParam, Message, TextBlock
from langfuse import get_client, observe

langfuse = get_client()


@observe(name="claude-response", as_type="generation")
def call_anthropic(
    client: anthropic.Anthropic,
    model: str,
    max_tokens: int,
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
    langfuse.update_current_generation(model=model, input=[{"role": "system", "content": prompt}] + history)

    result = client.messages.create(
        model=model,
        max_tokens=max_tokens,
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
