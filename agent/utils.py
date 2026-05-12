"""
TODO: description
"""


from typing import Any
import agent.cfg as cfg
from game_integration.factorio_bridge import execute_lua
import re
import factorio_rcon



def _extract(tag: str, text: str) -> str | None:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    return m.group(1).strip() if m else None

def parse_executor_agent_output(text: str) -> dict[str, Any]:
    """
    Parse agent XML response into a structured dict.

    Expected format:
        <thought>...</thought>
        <action>...</action>
        <skill_reused>none|true|false</skill_reused>
        <skill_name>name</skill_name>         (only when skill_reused=true)
        <new_skill_name>name</new_skill_name> (only when skill_reused=false)
        <task_complete>false|true</task_complete>

    Returns:
        {
            "thought":        str,
            "action":         str,
            "skill_reused":   "none" | "true" | "false",
            "skill_name":     str | None,
            "new_skill_name": str | None,
            "task_complete":           bool,
        }
    """

    skill_reused_raw = _extract("skill_reused", text) or "none"

    return {
        "thought": _extract("thought", text) or "",
        "action": _extract("action", text) or "",
        "skill_reused": skill_reused_raw,
        "existing_skill_name": _extract("existing_skill_name", text) if skill_reused_raw == "true" else None,
        "new_skill_name": _extract("new_skill_name", text) if skill_reused_raw == "false" else None,
        "task_complete": (_extract("task_complete", text) or "").lower() == "true",
    }

def parse_planner_agent_output(text: str) -> dict:
    """
    TODO: description
    """
    reasoning = _extract("reasoning", text) or ""
    plan_text = _extract("plan", text)
    
    # Parse the plan into a list by splitting on numbered lines
    if plan_text:
        # Splits by "1. ", "2. ", etc., while removing empty strings
        plan = [line.strip() for line in re.split(r'\d+\.\s+', plan_text) if line.strip()]
    else:
        plan = []
        
    return {"reasoning": reasoning, "plan": plan}


def save_skill(skill_name: str, lua_code: str) -> None:
    """
    TODO: description
    """
    cfg.SKILLS_DIR.mkdir(exist_ok=True)
    (cfg.SKILLS_DIR / f"{skill_name}.lua").write_text(f"{lua_code}", encoding="utf-8")
    print(f"skill saved: {skill_name}")


def reuse_skill(factorio_client: factorio_rcon.RCONClient, skill_name: str) -> dict[str, str]:
    """Execute a saved skill by name and return the result."""
    skill_file = cfg.SKILLS_DIR / f"{skill_name}.lua"
    lua_code = skill_file.read_text(encoding="utf-8")
    print(f"skill reused: {skill_name}")
    return execute_lua(factorio_client, lua_code)


if __name__ == "__main__":
    from agent.prompt import planner_prompt
    parsed = parse_planner_agent_output(planner_prompt.PLANNER_PROMPT)
    print(parsed)