"""
metrics/unique_items.py — Track unique item types ever gained by the player.

unique_items_produced grows monotonically: once an item name is seen entering
the inventory (current_count > previous_count), it stays in the set for the
lifetime of the process. Called from compute_reward() in metrics.py.
"""

unique_items_produced: set[str] = set()


def update_unique_items(
    current: dict[str, int],
    previous: dict[str, int],
) -> None:
    """
    Add to unique_items_produced any item whose count increased since previous.

    Args:
        current:  inventory snapshot from this step.
        previous: inventory snapshot from the previous step.
    """
    for name, count in current.items():
        if count > previous.get(name, 0):
            unique_items_produced.add(name)


def get_unique_items_produced() -> set[str]:
    """Return a copy of the set of item names ever gained by the player."""
    return set(unique_items_produced)