"""
test_primitives.py

Tests every primitive in basic_operations.py against a live Factorio game.
Run on a fresh map with no player-built entities.

Usage:
    python test_primitives.py

Each test prints PASS / FAIL with details.
"""

import sys
import time

from game_integration.factorio_bridge import execute_lua
from game_integration.dependencies import get_factorio_client
from game_integration.basic_operations import (
    find_ore_patch,
    find_valid_pump_position,
    get_entities,
    get_inventory,
    mine_resource,
    chop_tree,
    craft_item,
    insert_item,
    take_item,
    place_entity,
    remove_entity,
    rotate_entity,
    wait_seconds,
    wait_for_item,
)


# ---------------------------------------------------------------------------
# Test framework
# ---------------------------------------------------------------------------

_results: list[tuple[str, bool, str]] = []


def test(name: str, result: dict, check_fn=None, expect_fail: bool = False):
    if expect_fail:
        ok = not result["success"]
        reason = f"correctly failed: {result['message']}" if ok else f"should have failed but got: {result['message']}"
    else:
        ok = result["success"]
        reason = result["message"]

    if ok and check_fn is not None:
        check_ok, check_msg = check_fn(result.get("data", {}))
        if not check_ok:
            ok = False
            reason = check_msg

    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}: {reason}")
    _results.append((name, ok, reason))
    return ok


def give(client, **items):
    inserts = "\n".join(
        f'game.players[1].insert{{name="{k}", count={v}}}'
        for k, v in items.items()
    )
    execute_lua(client, inserts)


def clear_inventory(client):
    execute_lua(client, "game.players[1].get_main_inventory().clear()")


def clear_entities(client):
    execute_lua(client, """
local surface = game.players[1].surface
local ents = surface.find_entities_filtered{force="player"}
for _, e in ipairs(ents) do
    if e.type ~= "character" then e.destroy() end
end
""")


def section(title: str):
    print(f"\n{'='*50}")
    print(f" {title}")
    print(f"{'='*50}")


def get_ore_pos(client, resource, offset_x=10, offset_y=10):
    """Helper to get a safe integer position near an ore patch."""
    r = find_ore_patch(client, resource, near_x=0, near_y=0)
    if r["success"]:
        pos = r["data"]["positions"][0]
        # Round to integer for 2x2 entity placement
        return float(round(pos["x"]) + offset_x), float(round(pos["y"]) + offset_y)
    return float(50 + offset_x), float(50 + offset_y)


def find_clear_ground(client, near_x=0, near_y=0, distance=30) -> tuple[float, float]:
    """Find clear ground well away from spawn crash site for placing test entities."""
    from game_integration.factorio_bridge import execute_lua as _exe
    lua = f"""
local surface = game.players[1].surface
local near_x, near_y = {near_x}, {near_y}
-- Search in expanding rings, skip area near spawn (crash site within ~20 tiles)
-- Use step of 6 to only hit integer positions suitable for 2x2 entities
for radius = 30, 80, 6 do
    for angle = 0, 360, 30 do
        local rad = math.rad(angle)
        local x = math.floor(near_x + radius * math.cos(rad))
        local y = math.floor(near_y + radius * math.sin(rad))
        local can = surface.can_place_entity{{
            name="stone-furnace", position={{x, y}}, force="player"
        }}
        if can then
            -- Also verify entity stays after placement attempt
            local e = surface.create_entity{{
                name="stone-furnace", position={{x, y}},
                force="player", raise_built=true
            }}
            if e and e.valid then
                e.destroy()
                rcon.print(x .. "," .. y)
                return
            end
        end
    end
end
rcon.print("NONE")
"""
    result = _exe(client, lua)
    out = result.get("output", "").strip()
    if out and out != "NONE":
        parts = out.split(",")
        return float(parts[0]), float(parts[1])
    return float(near_x + distance), float(near_y + distance)


# ---------------------------------------------------------------------------
# 1. find_ore_patch
# ---------------------------------------------------------------------------

def test_find_ore_patch(client):
    section("1. find_ore_patch")

    for resource in ("iron-ore", "coal", "copper-ore", "stone"):
        r = find_ore_patch(client, resource, near_x=0, near_y=0)
        test(f"find {resource}", r,
             check_fn=lambda d: (len(d.get("positions", [])) > 0, "no positions returned"))

    # Positions have required fields
    r = find_ore_patch(client, "iron-ore", near_x=0, near_y=0)
    if r["success"]:
        pos = r["data"]["positions"][0]
        test("position has x/y/amount fields", r,
             check_fn=lambda d: (
                 all(k in d["positions"][0] for k in ("x", "y", "amount")),
                 f"missing fields: {pos}"
             ))
        print(f"    → nearest iron-ore at ({pos['x']},{pos['y']}) amount={pos['amount']}")

    # Should return sorted by distance
    r = find_ore_patch(client, "coal", near_x=0, near_y=0, max_results=3)
    if r["success"] and len(r["data"]["positions"]) >= 2:
        p0 = r["data"]["positions"][0]
        p1 = r["data"]["positions"][1]
        d0 = p0["x"]**2 + p0["y"]**2
        d1 = p1["x"]**2 + p1["y"]**2
        test("positions sorted by distance", r,
             check_fn=lambda d: (d0 <= d1, f"not sorted: {d0:.0f} > {d1:.0f}"))

    # Nonexistent resource fails
    r = find_ore_patch(client, "uranium-ore", near_x=0, near_y=0, search_radius=30)
    test("nonexistent resource fails", r, expect_fail=True)


# ---------------------------------------------------------------------------
# 2. find_valid_pump_position
# ---------------------------------------------------------------------------

def test_find_valid_pump_position(client):
    section("2. find_valid_pump_position")

    r = find_valid_pump_position(client, near_x=0, near_y=0)
    test("find pump position", r,
         check_fn=lambda d: (len(d.get("positions", [])) > 0, "no positions found"))

    if r["success"]:
        pos = r["data"]["positions"][0]
        test("pump position has all fields", r,
             check_fn=lambda d: (
                 all(k in d["positions"][0] for k in ("x","y","facing_x","facing_y")),
                 f"missing fields: {pos}"
             ))
        print(f"    → pump at ({pos['x']},{pos['y']}) "
              f"facing ({pos['facing_x']},{pos['facing_y']})")


# ---------------------------------------------------------------------------
# 3. get_entities
# ---------------------------------------------------------------------------

def test_get_entities(client):
    section("3. get_entities")

    clear_entities(client)

    # Empty map
    r = get_entities(client)
    test("empty map returns empty list", r,
         check_fn=lambda d: (d.get("entities") == [], f"got {d.get('entities')}"))

    # Place entities and check
    ix, iy = find_clear_ground(client, 30, 30)
    give(client, **{"stone-furnace": 1, "burner-mining-drill": 1, "coal": 10})

    r_iron = find_ore_patch(client, "iron-ore", near_x=0, near_y=0)
    drill_x = float(round(r_iron["data"]["positions"][0]["x"])) if r_iron["success"] else ix
    drill_y = float(round(r_iron["data"]["positions"][0]["y"])) if r_iron["success"] else iy
    
    r_drill = place_entity(client, "burner-mining-drill", drill_x, drill_y,
                           output_to_x=drill_x, output_to_y=drill_y + 3)
    r_furnace = place_entity(client, "stone-furnace", ix, iy)
    print(f"    → drill placed: {r_drill['success']} {r_drill['message']}")
    print(f"    → furnace placed: {r_furnace['success']} {r_furnace['message']}")
    
    if r_drill["success"]:
        insert_item(client, drill_x, drill_y, "coal", 5)

    r = get_entities(client)
    test("finds placed entities", r,
         check_fn=lambda d: (
             len(d.get("entities", [])) >= 2,
             f"expected ≥2, got {len(d.get('entities',[]))}"
         ))

    # Area filter
    r_local = get_entities(client, near_x=ix, near_y=iy, radius=10)
    test("area filter works", r_local,
         check_fn=lambda d: (
             len(d.get("entities", [])) >= 2,
             f"expected ≥2 in area, got {len(d.get('entities',[]))}"
         ))

    # Check fields
    if r["success"]:
        drill = next((e for e in r["data"]["entities"]
                     if e["name"] == "burner-mining-drill"), None)
        furnace = next((e for e in r["data"]["entities"]
                       if e["name"] == "stone-furnace"), None)

        required = ("name","x","y","status","fuel","input","output",
                    "facing_x","facing_y","drop_x","drop_y","pickup_x","pickup_y")
        test("drill has all required fields", r,
             check_fn=lambda d: (
                 drill is not None and all(k in drill for k in required),
                 f"missing: {[k for k in required if k not in (drill or {})]}"
             ))
        test("furnace has all required fields", r,
             check_fn=lambda d: (
                 furnace is not None and all(k in furnace for k in required),
                 f"missing: {[k for k in required if k not in (furnace or {})]}"
             ))

        if drill:
            test("drill drop_x/drop_y are not None", r,
                 check_fn=lambda d: (
                     drill.get("drop_x") is not None,
                     f"drop_x is None for drill"
                 ))
            print(f"    → drill@({drill['x']},{drill['y']}) "
                  f"status={drill['status']} "
                  f"drops_to=({drill.get('drop_x')},{drill.get('drop_y')})")

        if furnace:
            print(f"    → furnace@({furnace['x']},{furnace['y']}) "
                  f"status={furnace['status']} fuel={furnace['fuel']}")

    clear_inventory(client)
    clear_entities(client)


# ---------------------------------------------------------------------------
# 4. get_inventory
# ---------------------------------------------------------------------------

def test_get_inventory(client):
    section("4. get_inventory")

    clear_inventory(client)
    r = get_inventory(client)
    test("empty inventory", r,
         check_fn=lambda d: (d.get("inventory") == {}, f"got {d.get('inventory')}"))

    give(client, coal=15, **{"iron-ore": 7, "wood": 3})
    r = get_inventory(client)
    test("inventory with items", r,
         check_fn=lambda d: (
             d["inventory"].get("coal") == 15 and
             d["inventory"].get("iron-ore") == 7 and
             d["inventory"].get("wood") == 3,
             f"got {d.get('inventory')}"
         ))

    clear_inventory(client)


# ---------------------------------------------------------------------------
# 5. mine_resource
# ---------------------------------------------------------------------------

def test_mine_resource(client):
    section("5. mine_resource")

    clear_inventory(client)

    for resource in ("stone", "coal", "iron-ore", "copper-ore"):
        r_patch = find_ore_patch(client, resource, near_x=0, near_y=0)
        assert r_patch["success"], f"No {resource} patch found"
        pos = r_patch["data"]["positions"][0]

        r = mine_resource(client, resource, near_x=pos["x"], near_y=pos["y"], count=10)
        test(f"mine 10 {resource}", r)

        r_inv = get_inventory(client)
        test(f"{resource} in inventory after mining", r_inv,
             check_fn=lambda d, res=resource: (
                 d["inventory"].get(res, 0) >= 10,
                 f"expected ≥10 {res}, got {d['inventory'].get(res, 0)}"
             ))
        clear_inventory(client)

    # Count capped at 20
    r_patch = find_ore_patch(client, "coal", near_x=0, near_y=0)
    pos = r_patch["data"]["positions"][0]
    r = mine_resource(client, "coal", near_x=pos["x"], near_y=pos["y"], count=99)
    test("count capped at 20", r,
         check_fn=lambda d: (d.get("mined", 0) <= 20, f"mined {d.get('mined')}"))

    # Nonexistent resource fails
    r = mine_resource(client, "uranium-ore", near_x=0, near_y=0, count=5)
    test("nonexistent resource fails", r, expect_fail=True)

    clear_inventory(client)


# ---------------------------------------------------------------------------
# 6. chop_tree
# ---------------------------------------------------------------------------

def test_chop_tree(client):
    section("6. chop_tree")

    clear_inventory(client)

    r = chop_tree(client, near_x=0, near_y=0)
    if r["success"]:
        test("chop tree", r)
        r_inv = get_inventory(client)
        test("wood in inventory", r_inv,
             check_fn=lambda d: (d["inventory"].get("wood", 0) > 0, "no wood"))
    else:
        print("    → no trees near origin, trying wider search...")
        r = chop_tree(client, near_x=0, near_y=0, search_radius=100)
        test("chop tree (wide search)", r)

    clear_inventory(client)


# ---------------------------------------------------------------------------
# 7. craft_item
# ---------------------------------------------------------------------------

def test_craft_item(client):
    section("7. craft_item")

    clear_inventory(client)

    # stone-furnace: 5 stone → 1 furnace (always enabled)
    give(client, stone=25)
    r = craft_item(client, "stone-furnace", 3)
    test("craft 3 stone-furnace", r)
    r_inv = get_inventory(client)
    test("furnaces in inventory", r_inv,
         check_fn=lambda d: (
             d["inventory"].get("stone-furnace", 0) >= 3,
             f"got {d['inventory'].get('stone-furnace', 0)}"
         ))

    # iron-gear-wheel: 2 iron-plate → 1 gear
    give(client, **{"iron-plate": 10})
    r = craft_item(client, "iron-gear-wheel", 4)
    test("craft 4 iron-gear-wheel", r)

    # transport-belt: 1 iron-plate + 1 gear → 2 belts
    give(client, **{"iron-plate": 5})
    r = craft_item(client, "transport-belt", 3)
    test("craft 3 transport-belt (yields 6)", r)

    # Locked recipe fails - use inserter which needs electronics tech
    # (electronics requires 10 copper plates, unlikely to be researched on fresh map)
    r = craft_item(client, "inserter", 1)
    test("locked recipe (inserter/electronics) fails", r, expect_fail=True)

    # Missing ingredients fails
    clear_inventory(client)
    r = craft_item(client, "stone-furnace", 1)
    test("craft without ingredients fails", r, expect_fail=True)

    # Nonexistent recipe fails
    r = craft_item(client, "fake-item-xyz", 1)
    test("nonexistent recipe fails", r, expect_fail=True)

    # count=0 fails
    r = craft_item(client, "stone-furnace", 0)
    test("count=0 fails", r, expect_fail=True)

    clear_inventory(client)


# ---------------------------------------------------------------------------
# 8+9. insert_item + take_item
# ---------------------------------------------------------------------------

def test_insert_and_take_item(client):
    section("8+9. insert_item + take_item")

    clear_inventory(client)
    clear_entities(client)

    fx, fy = find_clear_ground(client, 25, 25)
    give(client, **{"stone-furnace": 1, "coal": 20, "iron-ore": 20})
    place_entity(client, "stone-furnace", fx, fy)

    # Insert coal as fuel
    r = insert_item(client, fx, fy, "coal", 8)
    test("insert coal into furnace", r)

    # Insert iron-ore as input
    r = insert_item(client, fx, fy, "iron-ore", 10)
    test("insert iron-ore into furnace", r)

    # Verify via get_entities
    r_ents = get_entities(client, near_x=fx, near_y=fy, radius=3)
    furnace = next((e for e in r_ents["data"]["entities"]
                   if e["name"] == "stone-furnace"), None) if r_ents["success"] else None
    if furnace:
        test("furnace fuel has coal", r_ents,
             check_fn=lambda d: (furnace["fuel"].get("coal", 0) > 0, f"fuel={furnace['fuel']}"))
        test("furnace input has iron-ore", r_ents,
             check_fn=lambda d: (furnace["input"].get("iron-ore", 0) > 0, f"input={furnace['input']}"))

    # Insert without item fails
    clear_inventory(client)
    r = insert_item(client, fx, fy, "copper-plate", 5)
    test("insert without item in inventory fails", r, expect_fail=True)

    # Insert into nonexistent entity fails
    r = insert_item(client, 9999, 9999, "coal", 1)
    test("insert into nonexistent entity fails", r, expect_fail=True)

    # Wait for smelting
    print("    → waiting 20s for furnace to produce iron plates...")
    time.sleep(20)

    # Take plates
    r = take_item(client, fx, fy, "iron-plate", 5)
    test("take iron-plate from furnace", r)

    r_inv = get_inventory(client)
    test("iron-plate in inventory after take", r_inv,
         check_fn=lambda d: (
             d["inventory"].get("iron-plate", 0) > 0,
             f"no iron-plate: {d['inventory']}"
         ))

    # Take nonexistent item fails
    r = take_item(client, fx, fy, "copper-plate", 1)
    test("take item not in furnace fails", r, expect_fail=True)

    # Take from nonexistent entity fails
    r = take_item(client, 9999, 9999, "iron-plate", 1)
    test("take from nonexistent entity fails", r, expect_fail=True)

    clear_inventory(client)
    clear_entities(client)


# ---------------------------------------------------------------------------
# 10+11. place_entity + remove_entity
# ---------------------------------------------------------------------------

def test_place_and_remove_entity(client):
    section("10+11. place_entity + remove_entity")

    clear_inventory(client)
    clear_entities(client)

    fx, fy = find_clear_ground(client, 20, 20)
    give(client, **{"stone-furnace": 2, "transport-belt": 5,
                    "burner-mining-drill": 1, "inserter": 1})

    # Place furnace (no direction needed)
    r = place_entity(client, "stone-furnace", fx, fy)
    test("place stone-furnace", r,
         check_fn=lambda d: ("placed_at" in d, "no placed_at"))
    placed_pos = r["data"].get("placed_at", {"x": fx, "y": fy})
    print(f"    → placed at ({placed_pos['x']},{placed_pos['y']})")

    # Verify appears in get_entities
    r_ents = get_entities(client, near_x=fx, near_y=fy, radius=5)
    test("entity appears in get_entities", r_ents,
         check_fn=lambda d: (
             any(e["name"] == "stone-furnace" for e in d.get("entities", [])),
             "furnace not found"
         ))

    # Place belt with direction (output_to)
    bx, by = fx + 4, fy
    r = place_entity(client, "transport-belt", bx, by,
                     output_to_x=bx - 1, output_to_y=by)  # facing west
    test("place transport-belt with direction", r)

    # Place on occupied tile fails
    r = place_entity(client, "stone-furnace", placed_pos["x"], placed_pos["y"])
    test("place on occupied tile fails", r, expect_fail=True)

    # Place without item fails
    clear_inventory(client)
    r = place_entity(client, "inserter", fx + 8, fy)
    test("place without item in inventory fails", r, expect_fail=True)

    # Remove entity
    r = remove_entity(client, placed_pos["x"], placed_pos["y"])
    test("remove entity", r)

    r_inv = get_inventory(client)
    test("entity back in inventory after remove", r_inv,
         check_fn=lambda d: (
             d["inventory"].get("stone-furnace", 0) >= 1,
             f"no furnace in inventory: {d['inventory']}"
         ))

    # Remove nonexistent entity fails
    r = remove_entity(client, 9999, 9999)
    test("remove nonexistent entity fails", r, expect_fail=True)

    clear_inventory(client)
    clear_entities(client)


# ---------------------------------------------------------------------------
# 12. rotate_entity
# ---------------------------------------------------------------------------

def test_rotate_entity(client):
    section("12. rotate_entity")

    clear_inventory(client)
    clear_entities(client)

    r_iron = find_ore_patch(client, "iron-ore", near_x=0, near_y=0)
    ix = float(round(r_iron["data"]["positions"][0]["x"])) if r_iron["success"] else 40.0
    iy = float(round(r_iron["data"]["positions"][0]["y"])) if r_iron["success"] else 40.0
    give(client, **{"burner-mining-drill": 1})

    # Place drill facing south (on ore patch)
    r = place_entity(client, "burner-mining-drill", ix, iy,
                     output_to_x=ix, output_to_y=iy + 3)
    test("place drill facing south", r)

    if r["success"]:
        pos = r["data"]["placed_at"]

        # Check initial direction via get_entities
        r_ents = get_entities(client, near_x=pos["x"], near_y=pos["y"], radius=2)
        drill_before = next((e for e in r_ents["data"].get("entities", [])
                            if e["name"] == "burner-mining-drill"), None)

        # Rotate to face north
        r_rot = rotate_entity(client, pos["x"], pos["y"],
                               output_to_x=pos["x"], output_to_y=pos["y"] - 3)
        test("rotate drill to face north", r_rot)

        # Verify direction changed
        r_ents2 = get_entities(client, near_x=pos["x"], near_y=pos["y"], radius=2)
        drill_after = next((e for e in r_ents2["data"].get("entities", [])
                           if e["name"] == "burner-mining-drill"), None)

        if drill_before and drill_after:
            test("drop position changed after rotation", r_ents2,
                 check_fn=lambda d: (
                     drill_before.get("drop_y") != drill_after.get("drop_y"),
                     f"drop_y unchanged: before={drill_before.get('drop_y')} after={drill_after.get('drop_y')}"
                 ))
            print(f"    → before drop=({drill_before.get('drop_x')},{drill_before.get('drop_y')})"
                  f" after drop=({drill_after.get('drop_x')},{drill_after.get('drop_y')})")

    # Rotate nonexistent entity fails
    r = rotate_entity(client, 9999, 9999, output_to_x=9999, output_to_y=9998)
    test("rotate nonexistent entity fails", r, expect_fail=True)

    clear_inventory(client)
    clear_entities(client)


# ---------------------------------------------------------------------------
# 13. wait_seconds
# ---------------------------------------------------------------------------

def test_wait_seconds(client):
    section("13. wait_seconds")

    start = time.time()
    r = wait_seconds(client, 3)
    elapsed = time.time() - start
    test("wait 3 seconds", r,
         check_fn=lambda d: (2.5 <= elapsed <= 5, f"waited {elapsed:.1f}s"))
    print(f"    → waited {elapsed:.1f}s")

    # Capped at 30
    start = time.time()
    r = wait_seconds(client, 999)
    elapsed = time.time() - start
    test("wait capped at 30s", r,
         check_fn=lambda d: (elapsed <= 35, f"waited {elapsed:.1f}s"))


# ---------------------------------------------------------------------------
# 14. wait_for_item
# ---------------------------------------------------------------------------

def test_wait_for_item(client):
    section("14. wait_for_item")

    clear_inventory(client)
    clear_entities(client)

    fx, fy = find_clear_ground(client, 35, 35)
    give(client, **{"stone-furnace": 1, "coal": 20, "iron-ore": 20})

    place_entity(client, "stone-furnace", fx, fy)
    insert_item(client, fx, fy, "coal", 10)
    insert_item(client, fx, fy, "iron-ore", 10)

    # Wait for 1 plate — should succeed
    r = wait_for_item(client, fx, fy, "iron-plate", 1, timeout_seconds=30)
    test("wait for 1 iron-plate (should succeed)", r,
         check_fn=lambda d: (d.get("count", 0) >= 1, f"count={d.get('count')}"))

    # Wait for impossible count — should timeout
    r = wait_for_item(client, fx, fy, "copper-plate", 1000, timeout_seconds=5)
    test("wait timeout on impossible count", r, expect_fail=True)

    # Wait on nonexistent entity — should timeout
    r = wait_for_item(client, 9999, 9999, "iron-plate", 1, timeout_seconds=3)
    test("wait on nonexistent entity times out", r, expect_fail=True)

    clear_inventory(client)
    clear_entities(client)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def reset_research(client):
    """Reset all research so locked-recipe tests work correctly."""
    execute_lua(client, """
local force = game.forces.player
for name, tech in pairs(force.technologies) do
    tech.researched = false
end
force.reset_recipes()
force.reset_technologies()
""")
    print("  → research reset to fresh state")


def run_all(client):
    print("\n" + "="*50)
    print(" BASIC OPERATIONS TEST SUITE")
    print("="*50)
    print("Testing all 14 primitives against live Factorio...")
    print("Resetting research to fresh state...")
    reset_research(client)

    test_find_ore_patch(client)
    test_find_valid_pump_position(client)
    test_get_entities(client)
    test_get_inventory(client)
    test_mine_resource(client)
    test_chop_tree(client)
    test_craft_item(client)
    test_insert_and_take_item(client)
    test_place_and_remove_entity(client)
    test_rotate_entity(client)
    test_wait_seconds(client)
    test_wait_for_item(client)

    total  = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = total - passed

    print(f"\n{'='*50}")
    print(f" RESULTS: {passed}/{total} passed, {failed} failed")
    print(f"{'='*50}")

    if failed > 0:
        print("\nFailed tests:")
        for name, ok, reason in _results:
            if not ok:
                print(f"  ✗ {name}: {reason}")

    return failed == 0


if __name__ == "__main__":
    client = get_factorio_client()
    pong = execute_lua(client, "rcon.print('pong')")
    assert pong["output"].strip() == "pong", "Cannot connect to Factorio"
    print("Connected to Factorio.")

    success = run_all(client)
    sys.exit(0 if success else 1)


    # result = execute_lua(client, """
    # local surface = game.players[1].surface
    # local ents = surface.find_entities_filtered{force="player"}
    # local lines = {}

    # local status_names = {
    #     [defines.entity_status.working] = "WORKING",
    #     [defines.entity_status.normal] = "WORKING",
    #     [defines.entity_status.full_output] = "FULL_OUTPUT",
    #     [defines.entity_status.no_fuel] = "NO_FUEL",
    #     [defines.entity_status.no_ingredients] = "NO_INGREDIENTS",
    #     [defines.entity_status.waiting_for_space_in_destination] = "WAITING_FOR_SPACE",
    #     [defines.entity_status.no_power] = "NO_POWER",
    #     [defines.entity_status.networks_connected] = "WORKING",
    #     [defines.entity_status.disabled_by_control_behavior] = "DISABLED",
    # }

    # local function inv_to_string(inv)
    #     if not inv then return "" end
    #     local parts = {}
    #     for _, item in ipairs(inv.get_contents()) do
    #         local c = item.count
    #         if type(c) == "table" then c = c.count or 0 end
    #         table.insert(parts, item.name.."="..tostring(c))
    #     end
    #     return table.concat(parts, ";")
    # end

    # for _, e in ipairs(ents) do
    #     if e.type ~= "character" then
    #         local ok, err = pcall(function()
    #         local status = (e.status and status_names[e.status]) or "UNKNOWN"
    #         local fuel   = inv_to_string(e.get_inventory(defines.inventory.fuel))
    #         local input  = ""
    #         for _, inv_id in ipairs({defines.inventory.furnace_source,
    #                                     defines.inventory.assembling_machine_input}) do
    #             local s = inv_to_string(e.get_inventory(inv_id))
    #             if s ~= "" then input = s; break end
    #         end
    #         local output = ""
    #         for _, inv_id in ipairs({defines.inventory.furnace_result,
    #                                     defines.inventory.assembling_machine_output,
    #                                     defines.inventory.chest,
    #                                     defines.inventory.item_main}) do
    #             local s = inv_to_string(e.get_inventory(inv_id))
    #             if s ~= "" then output = s; break end
    #         end
    #         local fx, fy = "", ""
    #         local dx, dy = "", ""
    #         local px, py = "", ""
    #         if e.drop_position then
    #             dx = tostring(e.drop_position.x)
    #             dy = tostring(e.drop_position.y)
    #             fx = dx; fy = dy
    #         end
    #         if e.pickup_position then
    #             px = tostring(e.pickup_position.x)
    #             py = tostring(e.pickup_position.y)
    #             if fx == "" then fx = px; fy = py end
    #         end
    #         table.insert(lines, e.name.."|"..
    #             string.format("%.1f", e.position.x).."|"..
    #             string.format("%.1f", e.position.y).."|"..
    #             status.."|"..fuel.."|"..input.."|"..output.."|"..
    #             fx.."|"..fy.."|"..dx.."|"..dy.."|"..px.."|"..py)
    #         end)
    #         if not ok then
    #             table.insert(lines, "ERROR|0.0|0.0|"..tostring(err).."|||||||||")
    #         end
    #     end
    # end
    # if #lines == 0 then rcon.print("NONE")
    # else rcon.print(table.concat(lines, "||")) end
    # """)
    # print(repr(result))