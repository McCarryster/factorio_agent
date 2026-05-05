"""
metrics/skill_library.py — Read-only view of the agent's skill library on disk.

The agent saves discovered Python functions as individual .py files inside
SKILLS_DIR. This module exposes simple queries over that directory; writing
new skills is handled by the agent itself.
"""

from pathlib import Path

SKILLS_DIR = "skills/"


def get_skill_library_size(skills_dir: str = SKILLS_DIR) -> int:
    """
    Return the number of .py files in the skills directory.

    Args:
        skills_dir: path to the skills directory (default SKILLS_DIR).

    Returns:
        Count of .py files. Returns 0 if the directory does not exist.
    """
    p = Path(skills_dir)
    if not p.is_dir():
        return 0
    return sum(1 for f in p.iterdir() if f.suffix == ".py")


def get_skill_names(skills_dir: str = SKILLS_DIR) -> list[str]:
    """
    Return the skill names (filenames without .py extension), sorted alphabetically.

    Args:
        skills_dir: path to the skills directory (default SKILLS_DIR).

    Returns:
        Sorted list of skill names. Returns [] if the directory does not exist.
    """
    p = Path(skills_dir)
    if not p.is_dir():
        return []
    return sorted(f.stem for f in p.iterdir() if f.suffix == ".py")
