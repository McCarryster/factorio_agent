"""
Static validator for agent-generated Lua code.

Catches the most common "spawn items from nothing" patterns. This is a
heuristic — it can have false positives. The point is to nudge the agent
back toward legitimate item flows, not to be a perfect static analyzer.

Usage:
    issues = validate_lua(lua_code)
    if issues:
        # refuse to run; feed `issues` back to the agent so it can fix
        ...
"""

import re
from dataclasses import dataclass


@dataclass
class Issue:
    severity: str       # "ERROR" (block) or "WARN" (allow but flag)
    line: int
    rule: str
    message: str


# Patterns and their meaning. Order matters — first match wins per line.
_RULES = [
    (
        "ERROR",
        r"\bgame\s*\.\s*players?\s*\[\s*\d+\s*\]\s*\.\s*cheat_mode",
        "cheat_mode",
        "Setting cheat_mode is forbidden.",
    ),
    (
        "ERROR",
        r"\bforce\s*\.\s*research_all_technologies\b",
        "research_all",
        "Researching all technologies at once is forbidden — research must happen via lab production.",
    ),
    (
        "ERROR",
        r"\bplayer\s*\.\s*insert\s*\{",
        "player_insert",
        "player.insert{} spawns items into the player from nothing. Use mining/crafting to obtain items.",
    ),
    (
        "ERROR",
        r"\bget_main_inventory\s*\(\s*\)\s*\.\s*insert\s*\{",
        "main_inv_insert",
        "Inserting directly into player's main inventory spawns items. Use mining/crafting instead.",
    ),
    (
        "ERROR",
        r"\bcreate_resource\b",
        "create_resource",
        "create_resource spawns ore patches from nothing.",
    ),
    (
        "WARN",
        r"\.insert\s*\{[^}]*\bname\s*=\s*[\"'](?:wood|coal|iron-ore|copper-ore|stone|uranium-ore|raw-fish|sulfur|crude-oil)[\"']",
        "insert_raw_resource",
        "Inserting raw resources into anything is suspicious — these come from mining/chopping.",
    ),
    (
        "ERROR",
        r"\bplayer\s*\.\s*mine_entity\s*\(",
        "manual_resource_mining",
        "player.mine_entity() is FORBIDDEN. The goal is to build automation. "
        "To get coal/ore/stone, you MUST place a burner-mining-drill on the resource patch and wait. "
        "The drill will mine the resource over time and drop items at its output tile. "
        "You can collect from the output tile using inserters or another drill.",
    ),
]


# Patterns that indicate a balanced item transfer (insert paired with remove from a source).
# If the script contains a balancing remove for an inserted item, downgrade ERROR → WARN.
_REMOVE_PAT = re.compile(
    r"\.remove(?:_item)?\s*\{[^}]*\bname\s*=\s*[\"']([\w-]+)[\"']"
)
_INSERT_PAT = re.compile(
    r"\.insert\s*\{[^}]*\bname\s*=\s*[\"']([\w-]+)[\"']"
)

_CREATE_ENTITY_PAT = re.compile(
    r"create_entity\s*\{[^}]*\bname\s*=\s*[\"']([\w-]+)[\"']"
)

def validate_lua(code: str) -> list[Issue]:
    issues: list[Issue] = []
    inserted_names = set(_INSERT_PAT.findall(code))
    removed_names = set(_REMOVE_PAT.findall(code))
    balanced = inserted_names & removed_names

    # check create_entity calls are balanced with inv.remove
    created_names = set(_CREATE_ENTITY_PAT.findall(code))
    unbalanced_entities = created_names - removed_names
    if unbalanced_entities:
        issues.append(Issue(
            severity="ERROR",
            line=0,
            rule="create_entity_no_remove",
            message=f"create_entity used for {unbalanced_entities} without a matching inv.remove{{}} — "
                    "you must remove the item from player inventory after placing it. "
                    "If you don't have the item, you cannot place it.",
        ))

    for lineno, line in enumerate(code.splitlines(), start=1):
        # ignore comments
        stripped = re.sub(r"--.*$", "", line)
        for severity, pattern, rule, message in _RULES:
            if re.search(pattern, stripped):
                if rule in ("player_insert", "main_inv_insert"):
                    m = re.search(
                        r"\.insert\s*\{[^}]*\bname\s*=\s*[\"']([\w-]+)[\"']",
                        stripped,
                    )
                    if m and m.group(1) in balanced:
                        issues.append(Issue(
                            severity="WARN",
                            line=lineno,
                            rule=rule + "_balanced",
                            message=f"insert/remove pair for {m.group(1)} — looks balanced, but verify the source was an inventory you own.",
                        ))
                        break
                issues.append(Issue(
                    severity=severity, line=lineno, rule=rule, message=message,
                ))
                break
    return issues


def format_issues(issues: list[Issue]) -> str:
    if not issues:
        return ""
    lines = []
    for i in issues:
        lines.append(f"  [{i.severity}] line {i.line} ({i.rule}): {i.message}")
    return "\n".join(lines)


def has_blocking(issues: list[Issue]) -> bool:
    return any(i.severity == "ERROR" for i in issues)


# ============================================================
# Self-test
# ============================================================

if __name__ == "__main__":
    bad_skill = """
local player = game.players[1]
local drill = player.surface.create_entity{name='burner-mining-drill', position=player.position}
player.remove_item{name='burner-mining-drill', count=1}
local fuel = drill.get_fuel_inventory()
fuel.insert{name='wood', count=10}
"""

    good_skill = """
local player = game.players[1]
local drill = player.surface.create_entity{name='burner-mining-drill', position=player.position}
player.remove_item{name='burner-mining-drill', count=1}
local fuel = drill.get_fuel_inventory()
local stack = player.get_main_inventory().find_item_stack('wood')
if stack then
    local moved = fuel.insert(stack)
    player.remove_item{name='wood', count=moved}
end
"""

    cheating_skill = """
local player = game.players[1]
player.insert{name='iron-plate', count=100}
"""

    for name, src in [("bad (fuel dup)", bad_skill), ("good (proper transfer)", good_skill), ("cheating (spawn)", cheating_skill)]:
        print(f"\n--- {name} ---")
        issues = validate_lua(src)
        if not issues:
            print("  (no issues)")
        else:
            print(format_issues(issues))
        print(f"  blocks execution: {has_blocking(issues)}")