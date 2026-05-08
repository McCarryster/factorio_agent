"""
metrics/skill_library.lua — Read-only view of the agent's skill library on disk.

The agent saves discovered Python functions as individual .lua files inside
SKILLS_DIR. This module exposes simple queries over that directory; writing
new skills is handled by the agent itself.
"""

from pathlib import Path


def get_skill_library_size(skills_dir: Path) -> int:
    """
    Return the number of .lua files in the skills directory.

    Args:
        skills_dir: path to the skills directory (default SKILLS_DIR).

    Returns:
        Count of .lua files. Returns 0 if the directory does not exist.
    """
    p = Path(skills_dir)
    if not p.is_dir():
        return 0
    return sum(1 for f in p.iterdir() if f.suffix == ".lua")


def get_skill_names(skills_dir: Path) -> list[str]:
    """
    Return the skill names (filenames without .lua extension), sorted alphabetically.

    Args:
        skills_dir: path to the skills directory (default SKILLS_DIR).

    Returns:
        Sorted list of skill names. Returns [] if the directory does not exist.
    """
    p = Path(skills_dir)
    if not p.is_dir():
        return []
    return sorted(f.stem for f in p.iterdir() if f.suffix == ".lua")
