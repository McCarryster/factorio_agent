"""
TODO: description
"""


from typing import Any
import agent.cfg as cfg
from game_integration.factorio_bridge import execute_lua
import re
import factorio_rcon


def parse_agent_output(text: str) -> dict[str, Any]:
    """
    Parse agent XML response into a structured dict.

    Expected format:
        <thought>...</thought>
        <action>...</action>
        <skill_reused>none|true|false</skill_reused>
        <skill_name>name</skill_name>         (only when skill_reused=true)
        <new_skill_name>name</new_skill_name> (only when skill_reused=false)
        <done>false|true</done>

    Returns:
        {
            "thought":        str,
            "action":         str,
            "skill_reused":   "none" | "true" | "false",
            "skill_name":     str | None,
            "new_skill_name": str | None,
            "done":           bool,
        }
    """

    def extract(tag: str) -> str | None:
        m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
        return m.group(1).strip() if m else None

    skill_reused_raw = extract("skill_reused") or "none"

    return {
        "thought":        extract("thought") or "",
        "action":         extract("action") or "",
        "skill_reused":   skill_reused_raw,
        "existing_skill_name": extract("existing_skill_name") if skill_reused_raw == "true" else None,
        "new_skill_name": extract("new_skill_name") if skill_reused_raw == "false" else None,
        "done":           (extract("done") or "").lower() == "true",
    }


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