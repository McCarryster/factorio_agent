"""
metrics/skill_reuse.py — Tracks skill library reuse rate.

Counters are module-level and persist for the lifetime of the process.
"""

_total_executions: int = 0
_skill_reuse_count: int = 0


def record_execution(from_library: bool) -> None:
    """
    Record one agent code execution.

    Args:
        from_library: True if the code came from the saved skill library,
                      False if it was newly written.
    """
    global _total_executions, _skill_reuse_count
    _total_executions += 1
    if from_library:
        _skill_reuse_count += 1


def get_skill_reuse_rate() -> float:
    """
    Return the fraction of executions that reused a saved skill.

    Returns:
        skill_reuse_count / total_executions, or 0.0 if no executions yet.
    """
    if _total_executions == 0:
        return 0.0
    return _skill_reuse_count / _total_executions
