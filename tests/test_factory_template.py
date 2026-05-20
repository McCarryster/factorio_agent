"""
test_full_factory.py

Complete factory build test: coal mining + iron mining + smelting +
coal belt connection + steam power + power line to factory.

Run on a fresh map with no player-built entities.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from game_integration.dependencies import get_factorio_client
from game_integration.factorio_bridge import execute_lua
from game_integration.primitives import validate_actions, translate_to_lua, parse_execution_output
from game_integration.factory_blocks import (
    find_patch_edge,
    max_drills_on_patch,
    place_coal_mining,
    place_iron_mining,
    place_smelting,
    connect_coal_to_main_belt,
    find_water_position,
    place_steam_network,
    place_power_line,
)


def give_items(client, items: dict):
    """Give player a set of items."""
    inserts = "\n".join(
        f'game.players[1].insert{{name="{name}", count={count}}}'
        for name, count in items.items()
    )
    execute_lua(client, inserts)


def execute_actions(client, actions: list[dict], label: str, max_actions: int = 300):
    """Validate and execute a list of primitive actions."""
    errors = validate_actions(actions)
    if errors:
        print(f"  [VALIDATION ERROR] {label}: {errors}")
        return False

    lua = translate_to_lua(actions, max_actions=max_actions)
    result = execute_lua(client, lua)
    report = parse_execution_output(result["output"])

    ok = report.success and not report.errors
    status = "✓" if ok else "✗"
    print(f"  {status} {label}: {len(report.actions_taken)} placed", end="")
    if report.errors:
        print(f"  ERRORS: {report.errors[:2]}", end="")
    if report.budget_exceeded:
        print(f"  [BUDGET EXCEEDED]", end="")
    print()
    return ok


def build_factory(client):
    print("\n" + "="*60)
    print("BUILDING IRON PLATE FACTORY")
    print("="*60)

    # -----------------------------------------------------------------------
    # Step 1: Find patch positions
    # -----------------------------------------------------------------------
    print("\n[1/7] Finding ore patches...")

    coal_start = find_patch_edge(client, "coal", near_x=94, near_y=-3, edge="west")
    if not coal_start:
        print("  ✗ No pure coal patch found near (94,-3)")
        return False
    print(f"  Coal patch start: {coal_start}")

    iron_start = find_patch_edge(client, "iron-ore", near_x=50, near_y=-22, edge="west")
    if not iron_start:
        print("  ✗ No pure iron-ore patch found near (50,-22)")
        return False
    print(f"  Iron patch start: {iron_start}")

    n_coal = max_drills_on_patch(client, "coal",
                                  drill_x=coal_start[0], drill_y=coal_start[1],
                                  direction="EAST")
    n_iron = max_drills_on_patch(client, "iron-ore",
                                  drill_x=iron_start[0], drill_y=iron_start[1],
                                  direction="EAST")

    # Cap drills to reasonable number to avoid placing hundreds of entities
    n_coal = min(n_coal, 4)
    n_iron = min(n_iron, 6)

    print(f"  Coal drills: {n_coal}, Iron drills: {n_iron}")

    # -----------------------------------------------------------------------
    # Step 2: Generate all actions
    # -----------------------------------------------------------------------
    print("\n[2/7] Generating actions...")

    coal_actions, coal_out = place_coal_mining(
        drill_x=coal_start[0], drill_y=coal_start[1],
        num_drills=n_coal,
        seed_coal=15,
    )
    print(f"  Coal mining: {len(coal_actions)} actions, "
          f"output belt at game ({coal_out.belt_x+0.5:.1f}, {coal_out.belt_y:.1f})")

    iron_actions, iron_out = place_iron_mining(
        drill_x=iron_start[0], drill_y=iron_start[1],
        num_drills=n_iron,
        seed_coal=5,
    )
    print(f"  Iron mining: {len(iron_actions)} actions, "
          f"main belt at game ({iron_out.belt_x:.1f}, {iron_out.belt_y:.1f})")

    smelt_actions, smelt_out = place_smelting(
        furnace_x=iron_start[0],
        furnace_y=iron_start[1] - 4,
        num_furnaces=n_iron,
        chest_type="wooden-chest",
        seed_coal=5,
    )
    print(f"  Smelting: {len(smelt_actions)} actions, "
          f"output chest at game ({smelt_out.chest_x:.1f}, {smelt_out.chest_y:.1f})")

    # -----------------------------------------------------------------------
    # Step 3: Connect coal belt to main iron belt
    # -----------------------------------------------------------------------
    print("\n[3/7] Planning coal→iron belt connection...")

    # Main belt east end: turn tile at template x = max_drill_x + 2, game x = +0.5
    # max_drill_x = iron_start[0] + (n_iron-1)*3
    main_belt_east_x = iron_start[0] + (n_iron - 1) * 3 + 2  # template x, game adds 0.5
    main_belt_y = iron_out.belt_y  # game y

    connect_actions = connect_coal_to_main_belt(
        coal_output_x=coal_out.belt_x + 0.5,  # game x of coal belt west end
        coal_output_y=coal_out.belt_y,
        main_belt_x=main_belt_east_x,
        main_belt_y=main_belt_y,
    )
    print(f"  Connection: {len(connect_actions)} belt tiles")

    # -----------------------------------------------------------------------
    # Step 4: Find water and plan steam network
    # -----------------------------------------------------------------------
    print("\n[4/7] Finding water...")

    water = find_water_position(client, near_x=0, near_y=0)
    if not water:
        print("  ✗ No suitable water position found")
        return False
    print(f"  Water at: ({water[0]}, {water[1]}) facing {water[2]}")

    steam_actions, steam_out = place_steam_network(
        water_x=water[0], water_y=water[1],
        pump_direction=water[2],
    )
    print(f"  Steam engine at: ({steam_out.engine_x:.1f}, {steam_out.engine_y:.1f})")
    print(f"  Steam pole at:   ({steam_out.pole_x:.1f}, {steam_out.pole_y:.1f})")

    # -----------------------------------------------------------------------
    # Step 5: Power line from engine to factory
    # -----------------------------------------------------------------------
    print("\n[5/7] Planning power line...")

    # Route poles from steam engine to the smelting area
    factory_center_x = iron_start[0] + (n_iron // 2) * 3
    factory_center_y = iron_start[1] - 4  # furnace level

    power_actions = place_power_line(
        from_x=steam_out.pole_x,
        from_y=steam_out.pole_y,
        to_x=factory_center_x,
        to_y=factory_center_y,
        pole_spacing=7,
    )
    print(f"  Power line: {len(power_actions)} poles")

    # -----------------------------------------------------------------------
    # Step 6: Give player all needed items
    # -----------------------------------------------------------------------
    print("\n[6/7] Stocking player inventory...")

    total_belts = (len(coal_actions) + len(iron_actions) +
                   len(smelt_actions) + len(connect_actions))
    belt_count = sum(1 for a in coal_actions + iron_actions + smelt_actions + connect_actions
                     if a.get("entity_name") == "transport-belt") + 10

    give_items(client, {
        "burner-mining-drill":    n_coal + n_iron + 5,
        "stone-furnace":          n_iron + 2,
        "inserter":               n_coal * 2 + n_iron * 3 + 10,
        "transport-belt":         belt_count + len(connect_actions) + 10,
        "wooden-chest":           2,
        "offshore-pump":          1,
        "boiler":                 1,
        "steam-engine":           1,
        "pipe":                   10,
        "small-electric-pole":    len(power_actions) + 5,
        "coal":                   200,
    })
    print("  Done.")

    # -----------------------------------------------------------------------
    # Step 7: Execute all actions
    # -----------------------------------------------------------------------
    print("\n[7/7] Placing entities...")

    all_ok = True
    all_ok &= execute_actions(client, coal_actions,    "Coal mining block")
    all_ok &= execute_actions(client, iron_actions,    "Iron mining block")
    all_ok &= execute_actions(client, smelt_actions,   "Smelting block")
    all_ok &= execute_actions(client, connect_actions, "Coal→iron belt connection",
                              max_actions=200)
    all_ok &= execute_actions(client, steam_actions,   "Steam network")
    all_ok &= execute_actions(client, power_actions,   "Power line", max_actions=100)

    print()
    if all_ok:
        print("✓ Factory placed successfully!")
    else:
        print("✗ Some steps had errors — check inspect_map.py for details")

    print("\nRun inspect_map.py to see the full layout.")
    return all_ok


if __name__ == "__main__":
    # print("Connecting to Factorio...")
    client = get_factorio_client()

    build_factory(client)


    # print(f"coal_out.belt_x={coal_out.belt_x}, coal_out.belt_y={coal_out.belt_y}")
    # print(f"main_belt_east_x={main_belt_east_x}, main_belt_y={main_belt_y}")
    # print(f"connect actions: {len(connect_actions)}")
    # for a in connect_actions[:5]:
    #     print(f"  {a['entity_name']} @ ({a['x']},{a['y']}) {a['direction']}")

    lua = """
    local count = 0
    for _, entity in pairs(game.surfaces["nauvis"].find_entities()) do
        if entity.last_user ~= nil and entity.type ~= "resource" then
            entity.destroy()
            count = count + 1
        end
    end
    game.print("Removed " .. count .. " player-placed entities (resources preserved).")
    """
    execute_lua(client, lua)