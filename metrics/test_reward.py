"""
debug_metrics.py — Run this to isolate exactly where metrics.py breaks.
"""
import json, time
from game_integration.factorio_bridge import execute_lua
from game_integration.dependencies import get_client

# ---------------------------------------------------------------------------
# Minimal inline Lua tests — each returns a JSON string we can inspect
# ---------------------------------------------------------------------------

# Test 1: Can we read player inventory at all?
LUA_PLAYER_INV = """
local inv = game.players[1].get_main_inventory()
local out = {}
for name, count in pairs(inv.get_contents()) do
  out[#out+1] = '"' .. name .. '":' .. count
end
rcon.print('{' .. table.concat(out, ',') .. '}')
"""

# Test 2: get_contents() format probe — Factorio 2.x changed the return type
# of get_contents() from {name=count} to an array of ItemStack-like tables.
# This test prints the raw type of the first entry.
LUA_CONTENTS_FORMAT = """
local inv = game.players[1].get_main_inventory()
local contents = inv.get_contents()
local first_key, first_val = next(contents)
if first_key == nil then
  rcon.print('{"status":"empty"}')
elseif type(first_key) == "number" then
  -- Factorio 2.x: array of {name=..., count=...} tables
  rcon.print('{"format":"array","name":"' .. (first_val.name or "?") .. '","count":' .. (first_val.count or 0) .. '}')
else
  -- Factorio 1.x / classic: {[name] = count}
  rcon.print('{"format":"dict","name":"' .. first_key .. '","count":' .. first_val .. '}')
end
"""

# Test 3: production stats for one known item (stone-wall)
LUA_PROD_STAT_WALL = """
local force = game.forces["player"]
local ps = force.item_production_statistics
local produced = ps.get_flow_count{
  name = "stone-wall",
  input = true,
  precision_index = defines.flow_precision_index.ten_minutes
}
local consumed = ps.get_flow_count{
  name = "stone-wall",
  input = false,
  precision_index = defines.flow_precision_index.ten_minutes
}
rcon.print('{"produced":' .. produced .. ',"consumed":' .. consumed .. '}')
"""

# Test 4: simple container scan with explicit format handling
LUA_SAFE_SURFACE = """
local counts = {}

local function drain(inv)
  if not inv then return end
  local contents = inv.get_contents()
  -- Handle both Factorio 1.x dict and 2.x array format
  local first_k, first_v = next(contents)
  if first_k == nil then return end
  if type(first_k) == "number" then
    -- 2.x: array of {name=str, count=int}
    for _, entry in ipairs(contents) do
      counts[entry.name] = (counts[entry.name] or 0) + entry.count
    end
  else
    -- 1.x: {[name]=count}
    for name, count in pairs(contents) do
      counts[name] = (counts[name] or 0) + count
    end
  end
end

-- Player
for _, player in pairs(game.players) do
  if player.character then
    drain(player.get_main_inventory())
  end
end

local out = {}
for name, count in pairs(counts) do
  out[#out+1] = '"' .. name .. '":' .. count
end
rcon.print('{' .. table.concat(out, ',') .. '}')
"""


def run(client, label, lua):
    result = execute_lua(client, lua.strip())
    print(f"\n{'='*60}")
    print(f"TEST: {label}")
    print(f"  status : {result.get('status')}")
    print(f"  output : {result.get('output', '')[:400]}")
    return result


if __name__ == "__main__":
    client = get_client()

    # Baseline
    run(client, "player inventory (before insert)", LUA_PLAYER_INV)
    run(client, "get_contents() format probe (before insert)", LUA_CONTENTS_FORMAT)
    run(client, "stone-wall production stats (before insert)", LUA_PROD_STAT_WALL)
    run(client, "safe surface scan (before insert)", LUA_SAFE_SURFACE)

    print("\n>>> Inserting 2x stone-wall into player inventory …")
    execute_lua(client, "game.players[1].insert{name='stone-wall', count=2}")
    time.sleep(0.2)  # give the game a tick

    run(client, "player inventory (AFTER insert)", LUA_PLAYER_INV)
    run(client, "get_contents() format probe (AFTER insert)", LUA_CONTENTS_FORMAT)
    run(client, "stone-wall production stats (AFTER insert)", LUA_PROD_STAT_WALL)
    run(client, "safe surface scan (AFTER insert)", LUA_SAFE_SURFACE)