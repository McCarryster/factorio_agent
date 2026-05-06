
import tempfile
import time
from pathlib import Path
from game_integration.factorio_bridge import execute_lua, connect, is_error
from metrics.reward_t import load_recipes, build_value_table, compute_reward
from metrics.unique_items import get_unique_items_produced
from metrics.skill_library import get_skill_library_size, get_skill_names, SKILLS_DIR
from metrics.milestones import (
    MILESTONES, check_milestones, get_milestones_reached, get_next_milestone,
)
from metrics.technologies import get_technologies_researched, get_tech_tree_depth
import metrics.skill_reuse as skill_reuse

# ---------------------------------------------------------------------------
# Smoke test / inspection entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Connecting to Factorio RCON...")
    connect()
    print("Connected.\n")

    # -------------------------------------------------------------------------
    # execute_lua pcall wrapper
    # -------------------------------------------------------------------------
    print("--- execute_lua ---")

    # Valid Lua that prints something
    r = execute_lua("rcon.print('hello')")
    assert r["status"] == "OK", f"expected OK, got {r}"
    assert r["output"] == "hello", f"expected 'hello', got {r['output']!r}"
    assert not is_error(r)

    # Valid Lua that prints nothing → status OK, output ""
    r = execute_lua("local x = 1 + 1")
    assert r["status"] == "OK", f"expected OK, got {r}"
    assert r["output"] == "", f"expected empty output, got {r['output']!r}"
    assert not is_error(r)

    # Broken Lua → error
    r = execute_lua("qweqwe5sertsef246$Q#$RDaw^&^&^ is not valid lua @@@@")
    assert is_error(r), f"expected ERROR status, got {r}"
    print(f"  broken Lua error: {r['output']}")

    # Runtime error (nil indexing)
    r = execute_lua("local t = nil; rcon.print(t.x)")
    assert is_error(r), f"expected ERROR status, got {r}"
    print(f"  runtime error:   {r['output']}")

    print("execute_lua: all assertions passed\n")

    # -------------------------------------------------------------------------
    # Value table
    # -------------------------------------------------------------------------
    print("Loading recipe prototypes...")
    recipes = load_recipes()
    print(f"  {len(recipes)} recipes loaded\n")

    print("Computing value table (Bellman-Ford)...")
    values = build_value_table(recipes)
    print(f"  {len(values)} items with finite values\n")

    print(f"  {'item':<42} {'V(i)':>10}")
    print(f"  {'-'*42} {'-'*10}")
    for name, val in sorted(values.items(), key=lambda x: x[1]):
        print(f"  {name:<42} {val:>10.4f}")

    print()
    print("Computing reward(t)...")
    print("Snapshot 1 — baseline inventory...")
    total1, _ = compute_reward(values)
    print(f"  reward = {total1:.4f}  (first call: full inventory counted as delta)\n")

    print("Snapshot 2 — delta since snapshot 1 (do something in-game...")

    execute_lua("game.players[1].insert{name='stone-wall', count=2}")
    time.sleep(1)

    total2, breakdown = compute_reward(values)
    
    print(f"  reward(t) = {total2:.4f}\n")
    if breakdown:
        print(f"  {'item':<42} {'delta V':>14}")
        print(f"  {'-'*42} {'-'*14}")
        for name, contrib in breakdown.items():
            print(f"  {name:<42} {contrib:>+14.4f}")
    else:
        print("  (no inventory change detected)")

    unique = get_unique_items_produced()
    print(f"\nUnique items produced so far ({len(unique)}):")
    for name in sorted(unique):
        print(f"  {name}")

    # -------------------------------------------------------------------------
    # Skill library
    # -------------------------------------------------------------------------
    print("\n--- skill library ---")

    # Real skills dir (only .gitkeep, no .py files yet)
    real_size = get_skill_library_size(SKILLS_DIR)
    real_names = get_skill_names(SKILLS_DIR)
    print(f"Real skills dir ({SKILLS_DIR}): {real_size} skills — {real_names}")

    # Isolated temp dir: verify counting and naming with known files
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "mine_iron.py").write_text("")
        (tmp_path / "smelt_plates.py").write_text("")
        (tmp_path / "craft_belt.py").write_text("")
        (tmp_path / "notes.txt").write_text("")   # should be ignored

        size = get_skill_library_size(tmp)
        names = get_skill_names(tmp)
        print(f"Temp dir test: size={size}, names={names}")
        assert size == 3, f"expected 3, got {size}"
        assert names == ["craft_belt", "mine_iron", "smelt_plates"], f"unexpected order: {names}"

    # Missing dir: should return safe defaults
    assert get_skill_library_size("nonexistent/") == 0
    assert get_skill_names("nonexistent/") == []

    print("skill_library: all assertions passed")

    # -------------------------------------------------------------------------
    # Milestones — pure logic (no RCON needed)
    # -------------------------------------------------------------------------
    print("\n--- milestones ---")

    reached: set[str] = set()

    # Empty inventory → nothing reached
    newly = check_milestones({}, reached)
    assert newly == set(), f"expected nothing, got {newly}"
    assert get_next_milestone(reached) == MILESTONES[0]

    # Raw resource → first_resource_mined
    newly = check_milestones({"iron-ore": 5}, reached)
    assert "first_resource_mined" in newly, f"expected first_resource_mined, got {newly}"
    assert "first_resource_mined" in reached

    # Crafted item → first_craft
    newly = check_milestones({"iron-ore": 5, "iron-plate": 1}, reached)
    assert "first_craft" in newly
    assert "first_iron_plate" in newly

    # transport-belt → first_belt
    newly = check_milestones({"transport-belt": 3}, reached)
    assert "first_belt" in newly

    # assembling-machine-1 → first_assembler
    newly = check_milestones({"assembling-machine-1": 1}, reached)
    assert "first_assembler" in newly

    # >50 iron-plate → iron_smelting_automated
    newly = check_milestones({"iron-plate": 51}, reached)
    assert "iron_smelting_automated" in newly

    # Already reached milestones don't appear again
    newly = check_milestones({"iron-plate": 51}, reached)
    assert newly == set(), f"expected no new milestones, got {newly}"

    # get_milestones_reached returns in MILESTONES order
    ordered = get_milestones_reached(reached)
    assert ordered == [m for m in MILESTONES if m in reached]

    # get_next_milestone points at first unreached
    nxt = get_next_milestone(reached)
    assert nxt is not None
    assert nxt not in reached, f"{nxt} already reached"
    assert MILESTONES.index(nxt) < len(MILESTONES)

    print(f"  reached ({len(reached)}): {get_milestones_reached(reached)}")
    print(f"  next:    {nxt}")
    print("milestones: all assertions passed")

    # Live check against actual game inventory
    print("\n--- milestones (live) ---")
    from game_integration.factorio_bridge import get_player_inventory
    live_inv = get_player_inventory()
    live_reached: set[str] = set()
    check_milestones(live_inv, live_reached)
    print(f"  reached: {get_milestones_reached(live_reached)}")
    print(f"  next:    {get_next_milestone(live_reached)}")



    # -------------------------------------------------------------------------
    # Technologies — live RCON queries
    # -------------------------------------------------------------------------
    print("\n--- technologies ---")

    researched = get_technologies_researched()
    print(f"  researched techs ({len(researched)}): {sorted(researched)[:5]} ...")

    depth = get_tech_tree_depth()
    print(f"  tech tree depth: {depth}")

    assert isinstance(researched, list), "expected list"
    assert all(isinstance(t, str) for t in researched), "expected list of str"
    assert isinstance(depth, int), "expected int"
    assert depth >= 0, "depth must be non-negative"
    if not researched:
        assert depth == 0, "depth must be 0 when nothing researched"
    print("technologies: all assertions passed")

    # -------------------------------------------------------------------------
    # Skill reuse — pure Python counter logic
    # -------------------------------------------------------------------------
    print("\n--- skill_reuse ---")

    # Fresh state: rate is 0.0 with no executions
    assert skill_reuse.get_skill_reuse_rate() == 0.0

    skill_reuse.record_execution(from_library=False)
    skill_reuse.record_execution(from_library=False)
    skill_reuse.record_execution(from_library=True)

    rate = skill_reuse.get_skill_reuse_rate()
    assert rate == 1 / 3, f"expected 1/3, got {rate}"

    skill_reuse.record_execution(from_library=True)
    rate = skill_reuse.get_skill_reuse_rate()
    assert rate == 2 / 4, f"expected 0.5, got {rate}"

    print(f"  reuse rate after 4 executions (2 library): {rate:.2f}")
    print("skill_reuse: all assertions passed")