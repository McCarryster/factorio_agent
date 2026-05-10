LUA_CODE_RULES = """
RULES FOR LUA CODE — RESOURCE INTEGRITY:
You must obtain items through legitimate game mechanics only. The following are FORBIDDEN:

- player.insert{...} for items you don't own — this spawns items from nothing
- inventory.insert{...} when not transferring from a source — this also spawns
- player.cheat_mode, force.research_all_technologies(), instant cheats
- game.create_inventory(), game.create_resource(), surface.create_entity() for items

You MAY do these (legitimate item flows):
- player.mine_entity(entity) — manual mining
- entity.mine{} — mining a placed entity
- Move items between inventories you already own:
    local stack = player.get_main_inventory().find_item_stack("wood")
    if stack then
        local moved = drill.get_fuel_inventory().insert(stack)
        player.get_main_inventory().remove{name="wood", count=moved}
    end
- Place entities you own:
    surface.create_entity{name="X", ...}      ← creates the entity
    player.remove_item{name="X", count=1}     ← REQUIRED to balance the books

When fueling a machine, you must REMOVE wood/coal/etc from the player's inventory equal to what you inserted, otherwise you have duplicated items.

WHY: the reward signal counts your total item value. Spawning items shows as positive reward but is cheating and will not transfer to real gameplay. Skills that duplicate items will be flagged and discarded.
"""