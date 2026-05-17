"""
build_planner_context.py

Converts raw entity status + production metrics into the structured
planner observation format.  No LLM involved — pure deterministic logic.

Usage:
    ctx = build_planner_context(goal, entities, throughput, inventory, techs)
    # ctx is a plain string ready to paste into the planner prompt
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

PROBLEM_STATUSES = {
    "NO_FUEL", "NO_POWER", "LOW_POWER", "NOT_PLUGGED_IN_ELECTRIC_NETWORK",
    "NO_RECIPE", "NO_INGREDIENTS", "ITEM_INGREDIENT_SHORTAGE",
    "FLUID_INGREDIENT_SHORTAGE", "NO_INPUT_FLUID", "LOW_INPUT_FLUID",
    "FULL_OUTPUT", "NOT_ENOUGH_SPACE_IN_OUTPUT", "FULL_BURNT_RESULT_OUTPUT",
    "NO_MINABLE_RESOURCES", "BROKEN", "MISSING_REQUIRED_FLUID",
    "WAITING_FOR_SOURCE_ITEMS", "WAITING_FOR_MORE_ITEMS",
    "WAITING_FOR_SPACE_IN_DESTINATION", "NETWORKS_DISCONNECTED",
}

WORKING_STATUSES = {"WORKING", "NORMAL", "CONNECTED"}

# Canonical system membership by entity name substring
_SYSTEM_MAP = {
    "offshore-pump":        "power",
    "boiler":               "power",
    "steam-engine":         "power",
    "heat-exchanger":       "power",
    "nuclear-reactor":      "power",
    "accumulator":          "power",
    "solar-panel":          "power",
    "electric-pole":        "power",

    "mining-drill":         "mining",

    "furnace":              "smelting",
    "assembling-machine":   "assembly",

    "transport-belt":       "belts",
    "underground-belt":     "belts",
    "splitter":             "belts",

    "inserter":             "inserters",

    "pipe":                 "fluid",
    "pipe-to-ground":       "fluid",
    "pump":                 "fluid",
    "storage-tank":         "fluid",
    "offshore-pump":        "power",   # already above, kept for clarity

    "wooden-chest":         "storage",
    "iron-chest":           "storage",
    "steel-chest":          "storage",
    "logistic-chest":       "storage",
}

def _system_of(entity: dict) -> str:
    name = entity["name"]
    for substr, system in _SYSTEM_MAP.items():
        if substr in name:
            return system
    return "other"

def _is_problem(entity: dict) -> bool:
    return entity["status"] in PROBLEM_STATUSES

def _is_working(entity: dict) -> bool:
    return entity["status"] in WORKING_STATUSES

def _pos(entity: dict) -> str:
    return f"({entity['x']},{entity['y']})"

def _label(entity: dict) -> str:
    return f"{entity['name']}@{_pos(entity)}"


# ---------------------------------------------------------------------------
# System-level analysis helpers
# ---------------------------------------------------------------------------

@dataclass
class SystemReport:
    name: str
    total: int = 0
    working: int = 0
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems and self.working == self.total and self.total > 0

    @property
    def absent(self) -> bool:
        return self.total == 0


def _analyse_power(entities: list[dict]) -> SystemReport:
    r = SystemReport("POWER SYSTEM")
    power_entities = [e for e in entities if _system_of(e) == "power"
                      and "electric-pole" not in e["name"]]
    r.total = len(power_entities)
    for e in power_entities:
        if _is_working(e):
            r.working += 1
        elif _is_problem(e):
            r.problems.append(f"{e['name']}@{_pos(e)}: {e['status']}")

    # Check poles separately — if any are NETWORKS_DISCONNECTED that's a real problem
    poles = [e for e in entities if "electric-pole" in e["name"]]
    disconnected = [e for e in poles if e["status"] == "NETWORKS_DISCONNECTED"]
    if disconnected:
        r.problems.append(f"{len(disconnected)} electric pole(s) disconnected from network")

    # Check how many electric consumers have NO_POWER
    no_power = [e for e in entities
                if e["status"] == "NO_POWER" and "inserter" in e["name"]]
    if no_power:
        r.notes.append(
            f"{len(no_power)} electric consumer(s) have NO_POWER "
            f"— power network may not reach them"
        )
    return r


def _analyse_mining(entities: list[dict]) -> SystemReport:
    r = SystemReport("IRON MINING SYSTEM")
    drills = [e for e in entities if "mining-drill" in e["name"]]
    r.total = len(drills)
    blocked = []
    for e in drills:
        if _is_working(e):
            r.working += 1
        else:
            blocked.append(e)
            r.problems.append(f"{_label(e)}: {e['status']}")
    if blocked:
        r.notes.append(f"{len(blocked)} drill(s) blocked")
    return r


def _analyse_smelting(entities: list[dict]) -> SystemReport:
    r = SystemReport("IRON SMELTING SYSTEM")
    furnaces = [e for e in entities if "furnace" in e["name"]]
    r.total = len(furnaces)
    for e in furnaces:
        if _is_working(e):
            r.working += 1
        elif _is_problem(e):
            r.problems.append(f"{_label(e)}: {e['status']}")

    # Inserters feeding furnaces
    inserters = [e for e in entities if "inserter" in e["name"]]
    no_power_ins = [e for e in inserters if e["status"] == "NO_POWER"]
    if no_power_ins:
        r.notes.append(f"{len(no_power_ins)} inserter(s) have NO_POWER")
    return r


def _analyse_belts(entities: list[dict]) -> SystemReport:
    r = SystemReport("BELT NETWORK")
    belts = [e for e in entities if _system_of(e) == "belts"]
    r.total = len(belts)
    for e in belts:
        if _is_working(e):
            r.working += 1
        elif _is_problem(e):
            r.problems.append(f"{_label(e)}: {e['status']}")
    return r


# ---------------------------------------------------------------------------
# Root-cause analysis
# ---------------------------------------------------------------------------

@dataclass
class RootCause:
    title: str
    description: str
    effects: list[str]
    affected: list[str]


def _find_root_causes(entities: list[dict]) -> list[RootCause]:
    causes: list[RootCause] = []

    # NO_POWER on inserters → likely power network gap
    no_power_inserters = [e for e in entities
                          if "inserter" in e["name"] and e["status"] == "NO_POWER"]
    if no_power_inserters:
        causes.append(RootCause(
            title="Electric inserters have no power",
            description=(
                "Electric inserters cannot operate without being within range of "
                "a powered electric pole. Either the power network doesn't reach "
                "these inserters, or the network has no generation."
            ),
            effects=[
                f"{len(no_power_inserters)} inserter(s) disabled",
                "item transport halted at all affected inserters",
                "downstream entities starved of inputs",
            ],
            affected=[_label(e) for e in no_power_inserters],
        ))

    # Furnaces with NO_INGREDIENTS
    starved_furnaces = [e for e in entities
                        if "furnace" in e["name"] and e["status"] == "NO_INGREDIENTS"]
    if starved_furnaces:
        causes.append(RootCause(
            title="Furnaces starved of ore",
            description="Furnaces have fuel but no input ore. Upstream inserters or drills are not delivering.",
            effects=["zero smelting throughput", "iron plate output halted"],
            affected=[_label(e) for e in starved_furnaces],
        ))

    # Drills WAITING_FOR_SPACE
    blocked_drills = [e for e in entities
                      if "mining-drill" in e["name"]
                      and e["status"] == "WAITING_FOR_SPACE_IN_DESTINATION"]
    if blocked_drills:
        causes.append(RootCause(
            title="Mining drills output blocked",
            description=(
                "Drills are mining but cannot eject ore because the output tile "
                "is full or the downstream belt/inserter is stalled."
            ),
            effects=["ore backs up", "drill throughput reduced"],
            affected=[_label(e) for e in blocked_drills],
        ))

    # NO_FUEL
    no_fuel = [e for e in entities if e["status"] == "NO_FUEL"]
    if no_fuel:
        causes.append(RootCause(
            title="Entities out of fuel",
            description="Burner entities have consumed all coal and stopped.",
            effects=["affected machines halted"],
            affected=[_label(e) for e in no_fuel],
        ))

    return causes


# ---------------------------------------------------------------------------
# Factory overall status
# ---------------------------------------------------------------------------

def _overall_status(
    systems: dict[str, SystemReport],
    throughput: dict[str, float],
) -> str:
    plate_rate = throughput.get("iron-plate", 0.0)
    has_problems = any(not r.ok and not r.absent for r in systems.values())

    if plate_rate > 0.1 and not has_problems:
        return "FULLY OPERATIONAL"
    if plate_rate > 0:
        return "OPERATIONAL WITH ISSUES"
    if any(not r.absent for r in systems.values()):
        return "PARTIALLY BUILT BUT NONFUNCTIONAL"
    return "NOTHING BUILT YET"


def _infrastructure_checklist(
    systems: dict[str, SystemReport],
    entities: list[dict],
    throughput: dict[str, float],
) -> list[tuple[bool, str]]:
    checks = []

    power = systems.get("power", SystemReport("power"))
    mining = systems.get("mining", SystemReport("mining"))
    smelting = systems.get("smelting", SystemReport("smelting"))
    belts = systems.get("belts", SystemReport("belts"))

    # Power
    has_engine = any("steam-engine" in e["name"] or "solar-panel" in e["name"]
                     for e in entities)
    checks.append((has_engine, "Power generation exists"))

    if has_engine:
        no_power_count = sum(1 for e in entities if e["status"] == "NO_POWER")
        checks.append((no_power_count == 0, "All consumers have power"))

    # Mining
    checks.append((not mining.absent, "Iron mining array exists"))
    if not mining.absent:
        checks.append((
            mining.working == mining.total,
            f"All {mining.total} drill(s) operational"
        ))

    # Smelting
    checks.append((not smelting.absent, "Smelting array exists"))
    if not smelting.absent:
        checks.append((
            smelting.working == smelting.total,
            f"All {smelting.total} furnace(s) operational"
        ))

    # Belts
    checks.append((not belts.absent, "Belt infrastructure exists"))

    # Throughput
    plate_rate = throughput.get("iron-plate", 0.0)
    checks.append((plate_rate > 0, f"Iron plates being produced ({plate_rate:.2f}/sec)"))

    return checks


# ---------------------------------------------------------------------------
# Resource flows
# ---------------------------------------------------------------------------

def _flow_section(entities: list[dict]) -> str:
    """Describe key item flows and where they break."""
    lines = []

    drills = [e for e in entities if "mining-drill" in e["name"]]
    furnaces = [e for e in entities if "furnace" in e["name"]]

    if drills:
        lines.append("FLOW: Ore mining → smelting")
        lines.append("")
        for d in drills:
            lines.append(f"  drill@{_pos(d)}  status:{d['status']}")
        if furnaces:
            for f in furnaces:
                lines.append(f"  furnace@{_pos(f)}  status:{f['status']}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Repair priority
# ---------------------------------------------------------------------------

def _repair_priorities(causes: list[RootCause]) -> list[tuple[str, str, str]]:
    """
    Returns list of (priority_label, description, success_condition).
    Ordered by what to fix first.
    """
    priorities = []
    seen = set()

    for cause in causes:
        title = cause.title
        if title in seen:
            continue
        seen.add(title)

        if "no power" in title.lower():
            priorities.append((
                "Reconnect power network to inserters",
                "Place small-electric-pole(s) to bridge the gap between the "
                "steam engine network and the inserters near the smelting array.",
                "all inserters status != NO_POWER",
            ))
        elif "starved" in title.lower():
            priorities.append((
                "Restore ore flow to furnaces",
                "Once inserters have power they should auto-resume. "
                "Verify ore is on the input belt.",
                "furnace status == WORKING",
            ))
        elif "blocked" in title.lower() and "drill" in title.lower():
            priorities.append((
                "Unblock mining drill output",
                "Check the tile the drill outputs to. Place a belt or chest "
                "to receive the ore.",
                "drill status == WORKING",
            ))
        elif "fuel" in title.lower():
            priorities.append((
                "Refuel burner machines",
                "Insert coal into the fuel slot of each stopped burner entity.",
                "affected machines status != NO_FUEL",
            ))

    if not priorities:
        priorities.append((
            "Investigate root cause",
            "No clear single root cause identified. Check BOTTLENECKS section.",
            "production rate > 0",
        ))

    return priorities


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_planner_context(
    goal: str,
    entities: list[dict],
    throughput: dict[str, float],
    inventory: dict[str, int],
    technologies: str,
    current_requirement: Optional[str] = None,
) -> str:
    """
    Build the structured planner context string.

    Args:
        goal: High-level goal string
        entities: Output of get_entity_status()
        throughput: dict of item_name -> rate_per_second
        inventory: dict of item_name -> count
        technologies: list of researched technology names
        current_requirement: Optional override for the "Current requirement" line
    """
    sep = "=" * 50

    # --- Analyse systems ---
    systems = {
        "power":    _analyse_power(entities),
        "mining":   _analyse_mining(entities),
        "smelting": _analyse_smelting(entities),
        "belts":    _analyse_belts(entities),
    }

    overall = _overall_status(systems, throughput)
    causes = _find_root_causes(entities)
    checklist = _infrastructure_checklist(systems, entities, throughput)
    priorities = _repair_priorities(causes)

    plate_rate = throughput.get("iron-plate", 0.0)

    out: list[str] = []

    # ---- HEADER ----
    out.append("=== GOAL ===")
    out.append(goal)
    out.append("")
    if current_requirement:
        out.append(f"Current requirement:\n{current_requirement}")
        out.append("")

    # ---- FACTORY SUMMARY ----
    out.append(sep)
    out.append("=== FACTORY SUMMARY ===")
    out.append("")
    out.append(f"Iron plate production:\n{plate_rate:.1f} plates/sec")
    out.append("")
    out.append(f"Factory status:\n{overall}")
    out.append("")
    out.append("Infrastructure:")
    for ok, label in checklist:
        marker = "[OK]" if ok else "[FAIL]"
        out.append(f"  {marker} {label}")
    out.append("")

    # ---- ROOT CAUSE ----
    out.append(sep)
    out.append("=== ROOT CAUSE ANALYSIS ===")
    out.append("")
    if not causes:
        out.append("No critical failures detected.")
    else:
        for i, cause in enumerate(causes):
            prefix = "PRIMARY" if i == 0 else "SECONDARY"
            out.append(f"{prefix} FAILURE:\n{cause.title}")
            out.append(f"\nDescription:\n{cause.description}")
            out.append("\nEffects:")
            for effect in cause.effects:
                out.append(f"  - {effect}")
            out.append("\nAffected entities:")
            for label in cause.affected[:8]:   # cap at 8 to save tokens
                out.append(f"  - {label}")
            if len(cause.affected) > 8:
                out.append(f"  ... and {len(cause.affected) - 8} more")
            out.append("")

    # ---- SYSTEMS ----
    out.append(sep)
    out.append("=== SYSTEMS ===")
    out.append("")

    for sys_name, report in systems.items():
        if report.absent:
            out.append(f"[{sys_name.upper()}]\nStatus: NOT BUILT\n")
            continue

        status_str = "OK" if report.ok else "DEGRADED" if report.working > 0 else "DOWN"
        out.append(f"[{sys_name.upper()}]")
        out.append(f"Status: {status_str}  ({report.working}/{report.total} operational)")
        if report.problems:
            out.append("Problems:")
            for p in report.problems[:6]:
                out.append(f"  - {p}")
            if len(report.problems) > 6:
                out.append(f"  ... and {len(report.problems) - 6} more")
        if report.notes:
            out.append("Notes:")
            for n in report.notes:
                out.append(f"  {n}")
        out.append("")

    # ---- RESOURCE FLOWS ----
    flow = _flow_section(entities)
    if flow.strip():
        out.append(sep)
        out.append("=== RESOURCE FLOWS ===")
        out.append("")
        out.append(flow)

    # ---- REPAIR PRIORITY ----
    out.append(sep)
    out.append("=== REPAIR PRIORITY ===")
    out.append("")
    for i, (action, desc, condition) in enumerate(priorities, 1):
        out.append(f"Priority {i}: {action}")
        out.append(f"  Plan: {desc}")
        out.append(f"  Success condition: {condition}")
        out.append("")

    # ---- AVAILABLE RESOURCES ----
    out.append(sep)
    out.append("=== AVAILABLE RESOURCES ===")
    out.append("")
    if inventory:
        for item, count in sorted(inventory.items()):
            out.append(f"  {item}: {count}")
    else:
        out.append("  (empty)")
    out.append("")

    # ---- TECHNOLOGIES ----
    out.append(sep)
    out.append("=== AVAILABLE TECHNOLOGIES ===")
    out.append("")
    if technologies:
        # for tech in technologies:
        out.append(f"  {technologies}")
    else:
        out.append("  none researched")
    out.append("")

    return "\n".join(out)