"""
metrics/technologies.py — Technology research state queries via RCON.
"""

from game_integration.factorio_bridge import execute_lua

_LUA_RESEARCHED = """
local out = {}
for name, tech in pairs(game.forces['player'].technologies) do
    if tech.researched then
        out[#out+1] = name
    end
end
rcon.print(table.concat(out, ','))
"""

_LUA_PREREQUISITES = """
local name = '%s'
local tech = game.forces['player'].technologies[name]
local out = {}
if tech then
    for prereq_name, _ in pairs(tech.prerequisites) do
        out[#out+1] = prereq_name
    end
end
rcon.print(table.concat(out, ','))
"""


def get_technologies_researched(client=None) -> list[str]:
    """
    Return names of all researched technologies for the player force.

    Args:
        client: RCON client. Uses module-level connection if omitted.

    Returns:
        List of researched technology name strings.
    """
    raw = execute_lua(_LUA_RESEARCHED.strip(), client)
    if not raw:
        return []
    return [t for t in raw.split(",") if t]


def get_tech_tree_depth(client=None) -> int:
    """
    Return the longest prerequisite chain among researched technologies.

    Fetches each researched technology's prerequisites and computes the
    longest path in the prerequisite DAG where every node is researched.
    Measures depth into the tech tree, not raw count.

    Args:
        client: RCON client. Uses module-level connection if omitted.

    Returns:
        Length of the longest fully-researched prerequisite chain (0 if
        nothing has been researched).
    """
    researched = set(get_technologies_researched(client))
    if not researched:
        return 0

    prereqs: dict[str, list[str]] = {}
    for name in researched:
        raw = execute_lua((_LUA_PREREQUISITES % name).strip(), client)
        prereqs[name] = [p for p in (raw or "").split(",") if p and p in researched]

    depth_cache: dict[str, int] = {}

    def _depth(name: str) -> int:
        if name in depth_cache:
            return depth_cache[name]
        result = 1 + max((_depth(p) for p in prereqs.get(name, [])), default=0)
        depth_cache[name] = result
        return result

    return max(_depth(name) for name in researched)
