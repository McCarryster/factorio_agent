"""
test_primitives.py

Integration tests for the primitive action layer.
Runs against a live Factorio game via RCON.

Usage:
    python3 test_primitives.py

Each test:
  - executes primitives against the real game
  - checks the execution report
  - verifies the world state changed as expected
  - cleans up after itself
"""

import sys

from game_integration.dependencies import get_factorio_client
from game_integration.factorio_bridge import execute_lua
from metrics.entity_status import get_entity_status
from game_integration.primitives import validate_actions, translate_to_lua, parse_execution_output


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

PASS = "✓"
FAIL = "✗"
results = []

def run_test(name: str, fn):
    try:
        fn()
        results.append((PASS, name))
        print(f"{PASS} {name}")
    except AssertionError as e:
        results.append((FAIL, name))
        print(f"{FAIL} {name}: {e}")
    except Exception as e:
        results.append((FAIL, name))
        print(f"{FAIL} {name}: EXCEPTION: {e}")


def execute(client, actions: list[dict], max_actions: int = 20):
    """Validate, translate, execute, parse — full pipeline."""
    errors = validate_actions(actions)
    assert not errors, f"Validation failed: {errors}"
    lua = translate_to_lua(actions, max_actions=max_actions)
    result = execute_lua(client, lua)
    assert result["status"] == "OK", f"Lua error: {result['output']}"
    return parse_execution_output(result["output"])


def get_entity_at(client, x, y, radius=1.0):
    """Return entity dict at position, or None."""
    lua = f"""
local e = game.surfaces['nauvis'].find_entities_filtered{{
    position = {{{x}, {y}}}, radius = {radius}
}}
for _, ent in ipairs(e) do
    if ent.type ~= "character" then
        rcon.print(ent.name .. ":" .. ent.position.x .. ":" .. ent.position.y)
        return
    end
end
rcon.print("NONE")
"""
    r = execute_lua(client, lua)
    if r["status"] != "OK" or r["output"].strip() == "NONE":
        return None
    parts = r["output"].strip().split(":")
    return {"name": parts[0], "x": float(parts[1]), "y": float(parts[2])}


def clear_position(client, x, y):
    """Remove any entity at position (cleanup helper)."""
    lua = f"""
local ents = game.surfaces['nauvis'].find_entities_filtered{{
    position = {{{x}, {y}}}, radius = 1.0
}}
for _, e in ipairs(ents) do
    if e.type ~= "character" then
        e.destroy()
    end
end
rcon.print("cleared")
"""
    execute_lua(client, lua)


def give_player_item(client, item_name: str, count: int):
    """Give the player items directly (test setup only)."""
    lua = f"""
game.players[1].insert{{name="{item_name}", count={count}}}
rcon.print("given")
"""
    execute_lua(client, lua)


def get_player_item_count(client, item_name: str) -> int:
    lua = f"""
rcon.print(game.players[1].get_item_count("{item_name}"))
"""
    r = execute_lua(client, lua)
    return int(r["output"].strip())


def get_entity_fuel(client, x, y, item_name: str) -> int:
    lua = f"""
local ents = game.surfaces['nauvis'].find_entities_filtered{{
    position = {{{x}, {y}}}, radius = 1.0
}}
for _, e in ipairs(ents) do
    if e.type ~= "character" then
        local inv = e.get_inventory(defines.inventory.fuel)
        if inv then
            rcon.print(inv.get_item_count("{item_name}"))
            return
        end
    end
end
rcon.print("0")
"""
    r = execute_lua(client, lua)
    return int(r["output"].strip())


# Use a remote area unlikely to conflict with the existing factory
TEST_X = 12
TEST_Y = -18


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_place_entity_success(client):
    """PLACE_ENTITY places a pole when player has the item."""
    clear_position(client, TEST_X, TEST_Y)
    give_player_item(client, "small-electric-pole", 1)

    report = execute(client, [{
        "type": "PLACE_ENTITY",
        "entity_name": "small-electric-pole",
        "x": TEST_X, "y": TEST_Y,
        "direction": "NORTH",
    }])

    assert report.success, f"Expected success, got errors: {report.errors}"
    assert len(report.actions_taken) == 1

    entity = get_entity_at(client, TEST_X, TEST_Y)
    assert entity is not None, "Entity not found in game after placement"
    assert "electric-pole" in entity["name"]

    clear_position(client, TEST_X, TEST_Y)


def test_place_entity_no_item(client):
    """PLACE_ENTITY fails gracefully when item not in inventory."""
    # Ensure player has none
    lua = f'game.players[1].remove_item{{name="small-electric-pole", count=999}}'
    execute_lua(client, lua)

    report = execute(client, [{
        "type": "PLACE_ENTITY",
        "entity_name": "small-electric-pole",
        "x": TEST_X, "y": TEST_Y,
        "direction": "NORTH",
    }])

    assert not report.success
    assert any("no_item" in e.detail for e in report.errors), \
        f"Expected no_item error, got: {report.errors}"

    entity = get_entity_at(client, TEST_X, TEST_Y)
    assert entity is None, "Entity should not have been placed"


def test_place_entity_blocked(client):
    """PLACE_ENTITY fails gracefully when tile is blocked."""
    clear_position(client, TEST_X, TEST_Y)
    give_player_item(client, "small-electric-pole", 2)

    # Place first pole
    execute(client, [{
        "type": "PLACE_ENTITY",
        "entity_name": "small-electric-pole",
        "x": TEST_X, "y": TEST_Y,
        "direction": "NORTH",
    }])

    # Try to place again on same tile
    report = execute(client, [{
        "type": "PLACE_ENTITY",
        "entity_name": "small-electric-pole",
        "x": TEST_X, "y": TEST_Y,
        "direction": "NORTH",
    }])

    assert not report.success
    assert any("blocked" in e.detail for e in report.errors), \
        f"Expected blocked error, got: {report.errors}"

    clear_position(client, TEST_X, TEST_Y)


def test_remove_entity_success(client):
    """REMOVE_ENTITY removes an entity and returns it to inventory."""
    clear_position(client, TEST_X, TEST_Y)
    give_player_item(client, "small-electric-pole", 1)

    # Place it first
    execute(client, [{
        "type": "PLACE_ENTITY",
        "entity_name": "small-electric-pole",
        "x": TEST_X, "y": TEST_Y,
        "direction": "NORTH",
    }])
    assert get_entity_at(client, TEST_X, TEST_Y) is not None

    # Now remove it
    report = execute(client, [{
        "type": "REMOVE_ENTITY",
        "x": TEST_X, "y": TEST_Y,
    }])

    assert report.success, f"Expected success: {report.errors}"
    entity = get_entity_at(client, TEST_X, TEST_Y)
    assert entity is None, "Entity should be gone after removal"


def test_remove_entity_not_found(client):
    """REMOVE_ENTITY fails gracefully when nothing is there."""
    clear_position(client, TEST_X, TEST_Y)

    report = execute(client, [{
        "type": "REMOVE_ENTITY",
        "x": TEST_X, "y": TEST_Y,
    }])

    assert not report.success
    assert any("not_found" in e.detail for e in report.errors)


def test_rotate_entity(client):
    """ROTATE_ENTITY changes entity direction."""
    clear_position(client, TEST_X, TEST_Y)
    give_player_item(client, "inserter", 1)

    execute(client, [{
        "type": "PLACE_ENTITY",
        "entity_name": "inserter",
        "x": TEST_X, "y": TEST_Y,
        "direction": "NORTH",
    }])

    report = execute(client, [{
        "type": "ROTATE_ENTITY",
        "x": TEST_X, "y": TEST_Y,
        "direction": "SOUTH",
    }])

    assert report.success, f"Rotate failed: {report.errors}"

    # Verify direction changed in game
    lua = f"""
local ents = game.surfaces['nauvis'].find_entities_filtered{{
    position = {{{TEST_X}, {TEST_Y}}}, radius = 1.0
}}
for _, e in ipairs(ents) do
    if e.type ~= "character" then
        rcon.print(e.direction)
        return
    end
end
rcon.print("-1")
"""
    r = execute_lua(client, lua)
    direction = int(r["output"].strip())
    assert direction == 8, f"Expected SOUTH (8), got {direction}"

    clear_position(client, TEST_X, TEST_Y)


def test_insert_item_success(client):
    """INSERT_ITEM puts coal into a burner drill's fuel slot."""
    clear_position(client, TEST_X, TEST_Y)
    give_player_item(client, "burner-mining-drill", 1)
    give_player_item(client, "coal", 20)

    execute(client, [{
        "type": "PLACE_ENTITY",
        "entity_name": "burner-mining-drill",
        "x": TEST_X, "y": TEST_Y,
        "direction": "NORTH",
    }])

    report = execute(client, [{
        "type": "INSERT_ITEM",
        "entity_x": TEST_X, "entity_y": TEST_Y,
        "item_name": "coal",
        "count": 5,
    }])

    assert report.success, f"INSERT_ITEM failed: {report.errors}"
    fuel = get_entity_fuel(client, TEST_X, TEST_Y, "coal")
    assert fuel > 0, f"No fuel found in entity after insert, got {fuel}"

    clear_position(client, TEST_X, TEST_Y)


def test_insert_item_no_item(client):
    """INSERT_ITEM fails gracefully when player has no coal."""
    clear_position(client, TEST_X, TEST_Y)
    give_player_item(client, "stone-furnace", 1)

    execute(client, [{
        "type": "PLACE_ENTITY",
        "entity_name": "stone-furnace",
        "x": TEST_X, "y": TEST_Y,
        "direction": "NORTH",
    }])

    # Remove coal from inventory
    execute_lua(client, 'game.players[1].remove_item{name="coal", count=999}')

    report = execute(client, [{
        "type": "INSERT_ITEM",
        "entity_x": TEST_X, "entity_y": TEST_Y,
        "item_name": "coal",
        "count": 5,
    }])

    assert not report.success
    assert any("no_item" in e.detail for e in report.errors)

    clear_position(client, TEST_X, TEST_Y)


def test_take_item_success(client):
    """TAKE_ITEM retrieves an item from an entity into player inventory."""
    clear_position(client, TEST_X, TEST_Y)
    give_player_item(client, "stone-furnace", 1)
    give_player_item(client, "coal", 10)

    execute(client, [{
        "type": "PLACE_ENTITY",
        "entity_name": "stone-furnace",
        "x": TEST_X, "y": TEST_Y,
        "direction": "NORTH",
    }])

    # Put coal into furnace
    execute(client, [{
        "type": "INSERT_ITEM",
        "entity_x": TEST_X, "entity_y": TEST_Y,
        "item_name": "coal",
        "count": 5,
    }])

    coal_before = get_player_item_count(client, "coal")

    report = execute(client, [{
        "type": "TAKE_ITEM",
        "entity_x": TEST_X, "entity_y": TEST_Y,
        "item_name": "coal",
        "count": 3,
    }])

    assert report.success, f"TAKE_ITEM failed: {report.errors}"
    coal_after = get_player_item_count(client, "coal")
    assert coal_after > coal_before, \
        f"Player coal didn't increase: before={coal_before} after={coal_after}"

    clear_position(client, TEST_X, TEST_Y)


def test_budget_enforcement(client):
    """max_actions budget stops execution after limit is reached."""
    clear_position(client, TEST_X, TEST_Y)
    clear_position(client, TEST_X + 3, TEST_Y)
    clear_position(client, TEST_X + 6, TEST_Y)
    give_player_item(client, "small-electric-pole", 3)

    actions = [
        {"type": "PLACE_ENTITY", "entity_name": "small-electric-pole",
         "x": TEST_X,     "y": TEST_Y, "direction": "NORTH"},
        {"type": "PLACE_ENTITY", "entity_name": "small-electric-pole",
         "x": TEST_X + 3, "y": TEST_Y, "direction": "NORTH"},
        {"type": "PLACE_ENTITY", "entity_name": "small-electric-pole",
         "x": TEST_X + 6, "y": TEST_Y, "direction": "NORTH"},
    ]

    # Budget of 1 — only first action should run
    report = execute(client, actions, max_actions=1)

    assert report.budget_exceeded, "Expected budget_exceeded flag"
    assert len(report.actions_taken) <= 1, \
        f"Expected at most 1 action taken, got {len(report.actions_taken)}"

    clear_position(client, TEST_X,     TEST_Y)
    clear_position(client, TEST_X + 3, TEST_Y)
    clear_position(client, TEST_X + 6, TEST_Y)


def test_validation_rejects_unknown_action(client):
    """validate_actions blocks unknown action types before touching game."""
    errors = validate_actions([{
        "type": "HALLUCINATED_ACTION",
        "x": 0, "y": 0,
    }])
    assert errors, "Expected validation error for unknown action"
    assert any("unknown action type" in str(e) for e in errors)


def test_validation_rejects_bad_direction(client):
    """validate_actions blocks invalid direction strings."""
    errors = validate_actions([{
        "type": "PLACE_ENTITY",
        "entity_name": "small-electric-pole",
        "x": 0.0, "y": 0.0,
        "direction": "UP",   # invalid
    }])
    assert errors
    assert any("direction" in str(e) for e in errors)


def test_validation_rejects_missing_field(client):
    """validate_actions catches missing required fields."""
    errors = validate_actions([{
        "type": "PLACE_ENTITY",
        "entity_name": "small-electric-pole",
        # missing x, y, direction
    }])
    assert errors
    assert any("missing" in str(e) for e in errors)


def test_multi_action_sequence(client):
    """Full sequence: place furnace, insert coal, remove furnace."""
    clear_position(client, TEST_X, TEST_Y)
    give_player_item(client, "stone-furnace", 1)
    give_player_item(client, "coal", 10)

    report = execute(client, [
        {"type": "PLACE_ENTITY",  "entity_name": "stone-furnace",
         "x": TEST_X, "y": TEST_Y, "direction": "NORTH"},
        {"type": "INSERT_ITEM",   "entity_x": TEST_X, "entity_y": TEST_Y,
         "item_name": "coal", "count": 5},
        {"type": "REMOVE_ENTITY", "x": TEST_X, "y": TEST_Y},
    ])

    assert len(report.actions_taken) == 3, \
        f"Expected 3 actions taken, got {len(report.actions_taken)}: {report.actions_taken}"
    assert not report.errors, f"Unexpected errors: {report.errors}"

    entity = get_entity_at(client, TEST_X, TEST_Y)
    assert entity is None, "Furnace should be removed at end of sequence"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Connecting to Factorio...\n")
    client = get_factorio_client()

    # Verify connection
    ping = execute_lua(client, "rcon.print('pong')")
    assert ping["output"].strip() == "pong", f"Connection failed: {ping}"
    print("Connected.\n")

    tests = [
        ("PLACE_ENTITY: success",           test_place_entity_success),
        ("PLACE_ENTITY: no item",           test_place_entity_no_item),
        ("PLACE_ENTITY: blocked tile",      test_place_entity_blocked),
        ("REMOVE_ENTITY: success",          test_remove_entity_success),
        ("REMOVE_ENTITY: not found",        test_remove_entity_not_found),
        ("ROTATE_ENTITY",                   test_rotate_entity),
        ("INSERT_ITEM: success",            test_insert_item_success),
        ("INSERT_ITEM: no item",            test_insert_item_no_item),
        ("TAKE_ITEM: success",              test_take_item_success),
        ("Budget enforcement",              test_budget_enforcement),
        ("Validation: unknown action",      test_validation_rejects_unknown_action),
        ("Validation: bad direction",       test_validation_rejects_bad_direction),
        ("Validation: missing field",       test_validation_rejects_missing_field),
        ("Multi-action sequence",           test_multi_action_sequence),
    ]

    print("Running tests...\n")
    for name, fn in tests:
        run_test(name, lambda f=fn: f(client))

    print(f"\n{'='*40}")
    passed = sum(1 for r, _ in results if r == PASS)
    failed = sum(1 for r, _ in results if r == FAIL)
    print(f"Results: {passed} passed, {failed} failed")

    if failed:
        sys.exit(1)