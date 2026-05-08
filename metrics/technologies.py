"""
metrics/technologies.py — Technology research state queries via RCON.
"""

from collections import deque
import factorio_rcon

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


def get_technologies_researched(client: factorio_rcon.RCONClient) -> list[str]:
    """
    Return names of all researched technologies for the player force.

    Args:
        client: RCON client. Uses module-level connection if omitted.

    Returns:
        List of researched technology name strings.
    """
    result = execute_lua(client, _LUA_RESEARCHED.strip())
    if result["status"] == "ERROR" or not result["output"]:
        return []
    return [t for t in result["output"].split(",") if t]


def get_tech_tree_depth(client: factorio_rcon.RCONClient) -> int:
    """
    Return the longest prerequisite chain among researched technologies.

    Fetches each researched technology's prerequisites and computes the
    longest path in the prerequisite DAG where every node is researched.
    Uses Kahn's topological sort so cycles and deep chains never cause
    recursion errors.

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
        result = execute_lua(client, (_LUA_PREREQUISITES % name).strip())
        raw = result["output"] if result["status"] == "OK" else ""
        prereqs[name] = [p for p in raw.split(",") if p and p in researched]

    # dependents[p] = techs that list p as a prerequisite
    dependents: dict[str, list[str]] = {n: [] for n in researched}
    in_degree: dict[str, int] = {n: len(prereqs[n]) for n in researched}
    for name, plist in prereqs.items():
        for p in plist:
            dependents[p].append(name)

    depth: dict[str, int] = {n: 1 for n in researched}
    queue: deque[str] = deque(n for n in researched if in_degree[n] == 0)

    while queue:
        node = queue.popleft()
        for dep in dependents[node]:
            depth[dep] = max(depth[dep], depth[node] + 1)
            in_degree[dep] -= 1
            if in_degree[dep] == 0:
                queue.append(dep)

    return max(depth.values())
