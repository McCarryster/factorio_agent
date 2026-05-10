"""
Test suite for RewardCalculator.

Verifies the key invariants:
  1. Inserting items gives positive reward (= sum of V values)
  2. Crafting from owned materials gives small positive reward (energy cost only)
  3. Placing an entity gives ~zero reward (V conserved: inventory item -> world entity)
  4. Mining own placed entity gives ~zero reward (entity -> back to inventory)
  5. Removing items gives negative reward (lost value)
  6. Idle steps give exactly zero
  7. V values look sensible for known items

Run from project root:
    python -m metrics.test_reward
"""

import time
import math

from game_integration.factorio_bridge import execute_lua, get_client
from metrics.reward_t import RewardCalculator   # adjust import path to your layout


# ============================================================
# Helpers
# ============================================================

def lua(client, code: str) -> str:
    """Run lua and return output, raising on error."""
    r = execute_lua(client, code)
    if r["status"] != "OK":
        raise RuntimeError(f"Lua error: {r['output']}")
    return r["output"]


def clear_player(client) -> None:
    """Clear inventory and any nearby entities so each test starts fresh."""
    lua(client, """
        local p = game.players[1]
        p.get_main_inventory().clear()
        p.get_inventory(defines.inventory.character_trash).clear()
        local r = 30
        for _, e in pairs(p.surface.find_entities_filtered{
            position=p.position, radius=r, force=p.force
        }) do
            if e.valid and e.type ~= "character" then e.destroy() end
        end
    """)


def insert(client, item: str, count: int = 1) -> None:
    lua(client, f"game.players[1].insert{{name='{item}', count={count}}}")


def remove(client, item: str, count: int = 1) -> None:
    lua(client, f"game.players[1].remove_item{{name='{item}', count={count}}}")


def place(client, item: str, dx: int = 3, dy: int = 0) -> None:
    """Place an entity from inventory at offset (dx,dy) from player."""
    lua(client, f"""
        local p = game.players[1]
        local pos = {{p.position.x + {dx}, p.position.y + {dy}}}
        local e = p.surface.create_entity{{
            name='{item}', position=pos, force=p.force
        }}
        if e then p.remove_item{{name='{item}', count=1}} end
    """)


def mine_nearest(client, item: str) -> None:
    """Find a nearby entity by name and mine it back into player inventory."""
    lua(client, f"""
        local p = game.players[1]
        for _, e in pairs(p.surface.find_entities_filtered{{
            name='{item}', position=p.position, radius=30
        }}) do
            if e.valid then
                -- collect drops, then destroy entity manually so loot lands in player inventory
                local products = e.prototype.mineable_properties.products or {{}}
                for _, prod in pairs(products) do
                    local amt = prod.amount or ((prod.amount_min or 0) + (prod.amount_max or 0)) / 2
                    if amt and amt > 0 then
                        p.insert{{name=prod.name, count=amt}}
                    end
                end
                e.destroy()
                break
            end
        end
    """)


def assert_close(actual: float, expected: float, tol: float = 0.5,
                 label: str = "") -> tuple[bool, str]:
    ok = abs(actual - expected) <= tol
    sign = "✓" if ok else "✗"
    msg = f"  {sign} {label}: got {actual:+.3f}, expected ~{expected:+.3f} (tol ±{tol})"
    return ok, msg


# ============================================================
# Test cases
# ============================================================

def run_tests(client, calc: RewardCalculator) -> None:
    results = []  # list of (name, passed, message)

    def test(name: str, expected: float, tol: float = 0.5):
        """Decorator-ish helper. Returns a function that runs the body, snaps reward, asserts."""
        def runner(body):
            clear_player(client)
            calc.reset()
            time.sleep(0.3)
            calc.reset()  # take real baseline AFTER state is settled

            body()
            time.sleep(0.5)  # let game tick
            r = calc.get_reward()
            ok, msg = assert_close(r, expected, tol, name)
            results.append((name, ok, msg))
            print(msg)
        return runner

    # ----- 1. Insert items -> positive reward -----
    @test("Insert 5x iron-plate", expected=5 * calc.V("iron-plate"), tol=0.5)
    def _():
        insert(client, "iron-plate", 5)

    @test("Insert 2x stone-furnace", expected=2 * calc.V("stone-furnace"), tol=1.0)
    def _():
        insert(client, "stone-furnace", 2)

    # ----- 2. Place entity -> reward ~= 0 (V conserved) -----
    @test("Place stone-furnace (V conserved)", expected=0.0, tol=0.5)
    def _():
        insert(client, "stone-furnace", 1)
        time.sleep(0.3)
        calc.reset()  # baseline = furnace in inventory
        place(client, "stone-furnace")

    @test("Place wooden-chest (V conserved)", expected=0.0, tol=0.5)
    def _():
        insert(client, "wooden-chest", 1)
        time.sleep(0.3)
        calc.reset()
        place(client, "wooden-chest", dx=4)

    # ----- 3. Mine own placed entity -> reward ~= 0 -----
    @test("Mine placed stone-furnace (V conserved)", expected=0.0, tol=0.5)
    def _():
        insert(client, "stone-furnace", 1)
        place(client, "stone-furnace")
        time.sleep(0.3)
        calc.reset()
        mine_nearest(client, "stone-furnace")

    # ----- 4. Remove items -> negative reward -----
    @test("Remove 3x iron-plate", expected=-3 * calc.V("iron-plate"), tol=0.5)
    def _():
        insert(client, "iron-plate", 3)
        time.sleep(0.3)
        calc.reset()
        remove(client, "iron-plate", 3)

    # ----- 5. Idle = 0 -----
    @test("Idle step", expected=0.0, tol=0.05)
    def _():
        pass

    # ----- 6. Materials -> craft entity should yield small positive (energy cost) -----
    # Insert raw materials, baseline, then directly insert the crafted product
    # while removing inputs (simulates a successful craft). Expected reward is small
    # because energy/complexity makes V(product) slightly > sum of input V.
    def _craft_test():
        clear_player(client)
        # 5 stone -> 1 stone-furnace
        insert(client, "stone", 5)
        time.sleep(0.3)
        calc.reset()
        # simulate craft: remove inputs, add output
        lua(client, """
            local p = game.players[1]
            p.remove_item{name='stone', count=5}
            p.insert{name='stone-furnace', count=1}
        """)
        time.sleep(0.5)
        r = calc.get_reward()
        v_in = 5 * calc.V("stone")
        v_out = calc.V("stone-furnace")
        delta = v_out - v_in
        ok, msg = assert_close(r, delta, tol=0.2, label="Craft stone-furnace from stone")
        results.append(("Craft stone-furnace", ok, msg))
        print(msg)
    _craft_test()

    # ----- 7. V values sanity check -----
    print("\n--- V values for common items ---")
    for item in ["iron-ore", "iron-plate", "iron-gear-wheel", "copper-cable",
                 "electronic-circuit", "stone-furnace", "transport-belt",
                 "inserter", "assembling-machine-1"]:
        v = calc.V(item)
        v_str = f"{v:.3f}" if math.isfinite(v) else "inf"
        print(f"  V({item}) = {v_str}")

    # ----- summary -----
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{'='*50}")
    print(f"Results: {passed}/{total} passed")
    if passed < total:
        print("Failed tests:")
        for name, ok, msg in results:
            if not ok:
                print(f"  {msg}")
    print('='*50)


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    client = get_client()
    print("Building reward calculator (this fetches recipe data)...")
    calc = RewardCalculator(client)
    print(f"Loaded {len(calc.recipes)} recipes, {len(calc.entity_to_item)} entity mappings\n")

    run_tests(client, calc)