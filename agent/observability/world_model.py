"""
semantic_world_model.py

Transforms raw entity observations into a semantic world model.

Pipeline:
    raw entities
        → build_entities()       typed objects with roles
        → build_relationships()  who feeds whom
        → detect_subsystems()    group into named systems
        → trace_power()          which consumers are powered
        → trace_item_flows()     ore → belt → furnace paths
        → diagnose()             root causes + downstream effects
        → generate_repairs()     ranked opportunities
        → summarize()            text for the LLM planner
"""

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class EntityRole(str, Enum):
    # Mining
    ORE_MINER       = "ore_miner"
    # Transport
    ORE_TRANSPORT   = "ore_transport"
    ITEM_TRANSPORT  = "item_transport"
    # Feeding
    ORE_FEEDER      = "ore_feeder"       # inserter: belt → furnace
    OUTPUT_COLLECTOR= "output_collector" # inserter: furnace → belt
    FUEL_FEEDER     = "fuel_feeder"      # inserter: chest → drill fuel
    COAL_FEEDER     = "coal_feeder"      # inserter: belt → furnace fuel
    # Processing
    SMELTER         = "smelter"
    ASSEMBLER       = "assembler"
    # Power
    POWER_SOURCE    = "power_source"     # steam-engine, solar
    POWER_DIST      = "power_dist"       # electric pole
    BOILER          = "boiler"
    PUMP            = "pump"
    # Storage
    STORAGE         = "storage"
    # Unknown
    UNKNOWN         = "unknown"


class SubsystemName(str, Enum):
    POWER    = "power"
    MINING   = "mining"
    SMELTING = "smelting"
    ASSEMBLY = "assembly"
    BELTS    = "belts"
    OTHER    = "other"


class FailureType(str, Enum):
    NO_POWER            = "NO_POWER"
    NO_FUEL             = "NO_FUEL"
    NO_INGREDIENTS      = "NO_INGREDIENTS"
    OUTPUT_BLOCKED      = "OUTPUT_BLOCKED"
    NO_GENERATION       = "NO_GENERATION"
    POWER_NETWORK_GAP   = "POWER_NETWORK_GAP"
    NO_ORE_FLOW         = "NO_ORE_FLOW"
    NOT_BUILT           = "NOT_BUILT"


class RepairType(str, Enum):
    PLACE_POLE          = "place_electric_pole"
    ADD_FUEL            = "add_fuel"
    ADD_ORE             = "add_ore"
    CLEAR_OUTPUT        = "clear_output"
    PLACE_DRILL         = "place_drill"
    PLACE_FURNACE       = "place_furnace"
    PLACE_INSERTER      = "place_inserter"
    PLACE_BELT          = "place_belt"


# ---------------------------------------------------------------------------
# Core data classes
# ---------------------------------------------------------------------------

@dataclass
class SemanticEntity:
    """One game entity with its semantic role attached."""
    raw: dict                           # original dict from get_entity_status()

    # Derived fields
    name: str        = field(init=False)
    x: float         = field(init=False)
    y: float         = field(init=False)
    status: str      = field(init=False)
    role: EntityRole = field(init=False, default=EntityRole.UNKNOWN)
    subsystem: SubsystemName = field(init=False, default=SubsystemName.OTHER)

    # Relationships (filled by build_relationships)
    upstream: list["SemanticEntity"]   = field(default_factory=list)
    downstream: list["SemanticEntity"] = field(default_factory=list)

    # Derived meaning (filled by diagnose)
    is_blocked: bool        = False
    block_reason: str       = ""
    downstream_effects: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.name    = self.raw["name"]
        self.x       = self.raw["x"]
        self.y       = self.raw["y"]
        self.status  = self.raw["status"]

    @property
    def pos(self) -> str:
        return f"({self.x},{self.y})"

    @property
    def label(self) -> str:
        return f"{self.name}@{self.pos}"

    @property
    def fuel(self) -> dict:
        return self.raw.get("fuel", {})

    @property
    def input_inv(self) -> dict:
        return self.raw.get("input", {})

    @property
    def output_inv(self) -> dict:
        return self.raw.get("output", {})

    @property
    def products_finished(self) -> int:
        return self.raw.get("products_finished", 0)


@dataclass
class Subsystem:
    """A named group of entities that work together toward one purpose."""
    name: SubsystemName
    entities: list[SemanticEntity] = field(default_factory=list)

    # Filled by diagnose()
    status: str = "UNKNOWN"          # OK / DEGRADED / DOWN / ABSENT
    throughput: float = 0.0          # items/sec if measurable
    bottlenecks: list[SemanticEntity] = field(default_factory=list)
    root_causes: list["RootCause"] = field(default_factory=list)

    @property
    def operational_count(self) -> int:
        return sum(1 for e in self.entities
                   if e.status in ("WORKING", "NORMAL", "CONNECTED",
                                   "WAITING_FOR_SPACE_IN_DESTINATION",
                                   "WAITING_FOR_SOURCE_ITEMS",
                                   "WAITING_FOR_MORE_ITEMS"))

    @property
    def total_count(self) -> int:
        return len(self.entities)


@dataclass
class RootCause:
    """A diagnosed failure with its downstream effects."""
    failure_type: FailureType
    description: str
    affected: list[SemanticEntity]
    downstream_effects: list[str]
    evidence: str = ""               # specific numeric/positional detail


@dataclass
class RepairOpportunity:
    """One concrete action the agent could take to fix something."""
    repair_type: RepairType
    description: str
    impact: str                      # HIGH / MEDIUM / LOW
    cost: str                        # LOW / MEDIUM / HIGH (resources needed)
    fixes: list[RootCause]
    suggested_position: Optional[tuple[float, float]] = None
    required_items: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Spatial helpers
# ---------------------------------------------------------------------------

DRILL_OUTPUT_OFFSETS = {0: (0,-2), 4: (2,0), 8: (0,2), 12: (-2,0)}
INSERTER_OFFSETS     = {0: (0,-1), 4: (1,0), 8: (0,1), 12: (-1,0)}
DIRECTION_NAMES      = {0:"NORTH", 4:"EAST", 8:"SOUTH", 12:"WEST"}

POLE_SUPPLY_RADIUS = {
    "small-electric-pole": 7.5,
    "medium-electric-pole": 9.5,
    "big-electric-pole": 30.0,
    "substation": 18.0,
}
POLE_WIRE_REACH = {
    "small-electric-pole": 9.0,
    "medium-electric-pole": 13.0,
    "big-electric-pole": 30.0,
    "substation": 18.0,
}

ELECTRIC_CONSUMERS = {
    "inserter", "electric-mining-drill", "assembling-machine",
    "electric-furnace", "lab", "radar", "lamp",
}

PROBLEM_STATUSES = {
    "NO_FUEL", "NO_POWER", "LOW_POWER", "NOT_PLUGGED_IN_ELECTRIC_NETWORK",
    "NO_RECIPE", "NO_INGREDIENTS", "ITEM_INGREDIENT_SHORTAGE",
    "NO_INPUT_FLUID", "LOW_INPUT_FLUID",
    "FULL_OUTPUT", "NOT_ENOUGH_SPACE_IN_OUTPUT",
    "NO_MINABLE_RESOURCES", "BROKEN",

    "NETWORKS_DISCONNECTED",
}

def _dist(ax, ay, bx, by) -> float:
    return ((ax-bx)**2 + (ay-by)**2)**0.5

def _drill_output(e: SemanticEntity) -> tuple[float, float]:
    dx, dy = DRILL_OUTPUT_OFFSETS.get(e.raw["direction"], (0, 2))
    return e.x + dx, e.y + dy

def _inserter_tiles(e: SemanticEntity) -> tuple[tuple, tuple]:
    dx, dy = INSERTER_OFFSETS.get(e.raw["direction"], (0, 0))
    return (e.x - dx, e.y - dy), (e.x + dx, e.y + dy)

def _pole_type(e: SemanticEntity) -> Optional[str]:
    for k in POLE_SUPPLY_RADIUS:
        if k in e.name:
            return k
    return None

def _nearest(
    entities: list[SemanticEntity],
    tx: float, ty: float,
    radius: float = 1.0,
    exclude: Optional[SemanticEntity] = None,
) -> Optional[SemanticEntity]:
    best, best_d = None, radius
    for e in entities:
        if exclude and id(e) == id(exclude):
            continue
        d = _dist(e.x, e.y, tx, ty)
        if d < best_d:
            best_d = d
            best = e
    return best


# ---------------------------------------------------------------------------
# Step 1 — build_entities
# ---------------------------------------------------------------------------

def _assign_role(raw: dict) -> EntityRole:
    name = raw["name"]
    if "mining-drill" in name:       return EntityRole.ORE_MINER
    if "furnace" in name:            return EntityRole.SMELTER
    if "assembling-machine" in name: return EntityRole.ASSEMBLER
    if "steam-engine" in name:       return EntityRole.POWER_SOURCE
    if "solar-panel" in name:        return EntityRole.POWER_SOURCE
    if "electric-pole" in name:      return EntityRole.POWER_DIST
    if "boiler" in name:             return EntityRole.BOILER
    if "offshore-pump" in name:      return EntityRole.PUMP
    if "transport-belt" in name:     return EntityRole.ITEM_TRANSPORT
    if "underground-belt" in name:   return EntityRole.ITEM_TRANSPORT
    if "splitter" in name:           return EntityRole.ITEM_TRANSPORT
    if "chest" in name:              return EntityRole.STORAGE
    if "inserter" in name:           return EntityRole.UNKNOWN  # refined later
    return EntityRole.UNKNOWN


def _assign_subsystem(role: EntityRole) -> SubsystemName:
    mapping = {
        EntityRole.ORE_MINER:       SubsystemName.MINING,
        EntityRole.SMELTER:         SubsystemName.SMELTING,
        EntityRole.ASSEMBLER:       SubsystemName.ASSEMBLY,
        EntityRole.POWER_SOURCE:    SubsystemName.POWER,
        EntityRole.POWER_DIST:      SubsystemName.POWER,
        EntityRole.BOILER:          SubsystemName.POWER,
        EntityRole.PUMP:            SubsystemName.POWER,
        EntityRole.ITEM_TRANSPORT:  SubsystemName.BELTS,
        EntityRole.STORAGE:         SubsystemName.OTHER,
    }
    return mapping.get(role, SubsystemName.OTHER)


def build_entities(raw_entities: list[dict]) -> list[SemanticEntity]:
    """Step 1: Convert raw dicts into SemanticEntity objects with roles."""
    result = []
    for raw in raw_entities:
        e = SemanticEntity(raw=raw)
        e.role = _assign_role(raw)
        e.subsystem = _assign_subsystem(e.role)
        result.append(e)
    return result


# ---------------------------------------------------------------------------
# Step 2 — build_relationships
# ---------------------------------------------------------------------------

def build_relationships(entities: list[SemanticEntity]) -> None:
    """
    Step 2: Link entities via upstream/downstream.

    For each inserter, find what it picks up from and drops into.
    For each drill, find what receives its output.
    Also refines inserter roles based on what they connect.
    """
    for e in entities:
        if "mining-drill" in e.name:
            ox, oy = _drill_output(e)
            target = _nearest(entities, ox, oy, radius=1.5, exclude=e)
            if target:
                e.downstream.append(target)
                target.upstream.append(e)

        elif "inserter" in e.name:
            (px, py), (dx, dy) = _inserter_tiles(e)
            src = _nearest(entities, px, py, radius=1.0, exclude=e)
            dst = _nearest(entities, dx, dy, radius=1.0, exclude=e)

            if src:
                e.upstream.append(src)
                src.downstream.append(e)
            if dst:
                e.downstream.append(dst)
                dst.upstream.append(e)

            # Refine inserter role
            src_name = src.name if src else ""
            dst_name = dst.name if dst else ""

            if "furnace" in dst_name or "assembling-machine" in dst_name:
                e.role = EntityRole.ORE_FEEDER
                e.subsystem = SubsystemName.SMELTING
            elif "furnace" in src_name or "assembling-machine" in src_name:
                e.role = EntityRole.OUTPUT_COLLECTOR
                e.subsystem = SubsystemName.SMELTING
            elif "mining-drill" in dst_name:
                e.role = EntityRole.FUEL_FEEDER
                e.subsystem = SubsystemName.MINING
            elif "mining-drill" in src_name:
                e.role = EntityRole.ORE_FEEDER
                e.subsystem = SubsystemName.MINING


# ---------------------------------------------------------------------------
# Step 3 — detect_subsystems
# ---------------------------------------------------------------------------

def detect_subsystems(entities: list[SemanticEntity]) -> dict[SubsystemName, Subsystem]:
    """Step 3: Group entities into named subsystems."""
    systems: dict[SubsystemName, Subsystem] = {
        name: Subsystem(name=name) for name in SubsystemName
    }
    for e in entities:
        systems[e.subsystem].entities.append(e)
    return systems


# ---------------------------------------------------------------------------
# Step 4 — trace_power
# ---------------------------------------------------------------------------

def trace_power(entities: list[SemanticEntity]) -> tuple[set[int], Optional[str]]:
    """
    Step 4: Flood-fill the power grid from generators.

    Returns:
        powered_ids   — set of id() for entities within supply radius
        gap_desc      — human description of the coverage gap, or None
    """
    poles      = [e for e in entities if _pole_type(e)]
    generators = [e for e in entities
                  if e.role == EntityRole.POWER_SOURCE]
    consumers  = [e for e in entities
                  if any(c in e.name for c in ELECTRIC_CONSUMERS)]

    if not generators or not poles:
        return set(), "No power generation or no poles placed."

    # Seed connected poles: any pole within wire reach of a generator
    connected: set[int] = set()
    for gen in generators:
        for pole in poles:
            pk = _pole_type(pole)
            if _dist(gen.x, gen.y, pole.x, pole.y) <= POLE_WIRE_REACH.get(pk, 9.0):
                connected.add(id(pole))

    # Expand: any pole within wire reach of a connected pole
    changed = True
    while changed:
        changed = False
        for pole in poles:
            if id(pole) in connected:
                continue
            pk = _pole_type(pole)
            wire = POLE_WIRE_REACH.get(pk, 9.0)
            for other in poles:
                if id(other) in connected and _dist(pole.x, pole.y, other.x, other.y) <= wire:
                    connected.add(id(pole))
                    changed = True
                    break

    connected_poles = [p for p in poles if id(p) in connected]

    # Which consumers are within supply radius of a connected pole?
    powered: set[int] = set()
    for pole in connected_poles:
        pk = _pole_type(pole)
        supply = POLE_SUPPLY_RADIUS.get(pk, 7.5)
        for c in consumers:
            if _dist(pole.x, pole.y, c.x, c.y) <= supply:
                powered.add(id(c))

    # Describe gap if any consumer is unpowered
    gap_desc = None
    unpowered = [c for c in consumers if id(c) not in powered]
    if unpowered and connected_poles:
        ue = unpowered[0]
        closest = min(connected_poles, key=lambda p: _dist(p.x, p.y, ue.x, ue.y))
        gap = _dist(closest.x, closest.y, ue.x, ue.y)
        pk = _pole_type(closest)
        supply = POLE_SUPPLY_RADIUS.get(pk, 7.5)
        shortfall = gap - supply
        gap_desc = (
            f"Nearest connected pole is {gap:.1f} tiles from "
            f"{ue.label} "
            f"(supply radius {supply} tiles — {shortfall:.1f} tiles short)"
        )

    return powered, gap_desc


# ---------------------------------------------------------------------------
# Step 5 — trace_item_flows
# ---------------------------------------------------------------------------

@dataclass
class ItemFlow:
    """One end-to-end item flow path: source → ... → processor."""
    source: SemanticEntity          # drill or chest
    feeder: Optional[SemanticEntity]   # inserter feeding processor
    processor: Optional[SemanticEntity]  # furnace or assembler
    collector: Optional[SemanticEntity]  # output inserter

    @property
    def is_broken(self) -> bool:
        for e in [self.source, self.feeder, self.processor, self.collector]:
            if e and e.status in PROBLEM_STATUSES:
                return True
        return False

    @property
    def first_broken(self) -> Optional[SemanticEntity]:
        for e in [self.source, self.feeder, self.processor, self.collector]:
            if e and e.status in PROBLEM_STATUSES:
                return e
        return None

    def summary(self) -> str:
        steps = []
        for e in [self.source, self.feeder, self.processor, self.collector]:
            if e is None:
                continue
            ok = "✓" if e.status not in PROBLEM_STATUSES else "✗"
            steps.append(f"    {ok} {e.label} [{e.status}]")
        return "\n".join(steps)


def trace_item_flows(entities: list[SemanticEntity]) -> list[ItemFlow]:
    """
    Step 5: Build ItemFlow objects for each drill.

    For each drill, find:
      - the inserter whose drop target is the nearest furnace
        (within 20 tiles of drill output)
      - the furnace that inserter feeds
      - the inserter that collects from that furnace
    """
    drills   = [e for e in entities if "mining-drill" in e.name]
    furnaces = [e for e in entities
                if "furnace" in e.name or "assembling-machine" in e.name]

    flows: list[ItemFlow] = []
    assigned_furnaces: set[int] = set()

    for drill in drills:
        ox, oy = _drill_output(drill)

        # Find the best (feeder inserter, furnace) pair for this drill:
        # the inserter whose pickup tile is closest to the drill output
        # and whose drop tile contains a furnace.
        best_feeder   = None
        best_furnace  = None
        best_dist     = float("inf")

        for e in entities:
            if "inserter" not in e.name:
                continue
            (px, py), (dx, dy) = _inserter_tiles(e)
            # Distance from this inserter's pickup to drill output
            d = _dist(px, py, ox, oy)
            if d > 20:
                continue
            # Does drop go to a furnace not yet claimed?
            target = _nearest(entities, dx, dy, radius=1.0, exclude=e)
            if target and ("furnace" in target.name or "assembling-machine" in target.name):
                if id(target) not in assigned_furnaces and d < best_dist:
                    best_dist    = d
                    best_feeder  = e
                    best_furnace = target

        if best_furnace:
            assigned_furnaces.add(id(best_furnace))

        # Find output collector (inserter whose pickup is the furnace)
        collector = None
        if best_furnace:
            for e in entities:
                if "inserter" not in e.name:
                    continue
                (px, py), _ = _inserter_tiles(e)
                src = _nearest(entities, px, py, radius=1.0, exclude=e)
                if src and id(src) == id(best_furnace):
                    collector = e
                    break

        flows.append(ItemFlow(
            source    = drill,
            feeder    = best_feeder,
            processor = best_furnace,
            collector = collector,
        ))

    return flows


# ---------------------------------------------------------------------------
# Step 6 — diagnose
# ---------------------------------------------------------------------------

def diagnose(
    entities: list[SemanticEntity],
    systems: dict[SubsystemName, Subsystem],
    powered_ids: set[int],
    gap_desc: Optional[str],
    flows: list[ItemFlow],
    throughput: dict[str, float],
) -> list[RootCause]:
    """
    Step 6: Derive root causes from the semantic model.

    Marks entities as blocked and sets downstream_effects.
    Updates subsystem statuses.
    Returns ordered list of RootCause (most impactful first).
    """
    causes: list[RootCause] = []

    # --- Mark entity block status ---
    for e in entities:
        if e.status in PROBLEM_STATUSES:
            e.is_blocked = True
            e.block_reason = e.status

    # --- Power network gap ---
    no_power = [e for e in entities
                if any(c in e.name for c in ELECTRIC_CONSUMERS)
                and id(e) not in powered_ids]
    if no_power:
        effects = []
        # Which subsystems are affected?
        affected_systems = {e.subsystem for e in no_power}
        for sys in affected_systems:
            effects.append(f"{sys.value} system inserters disabled")
        if any("inserter" in e.name and e.role == EntityRole.ORE_FEEDER
               for e in no_power):
            effects.append("furnaces receive no ore → zero smelting")

        causes.append(RootCause(
            failure_type     = FailureType.POWER_NETWORK_GAP,
            description      = "Power network does not reach all electric consumers.",
            affected         = no_power,
            downstream_effects = effects,
            evidence         = gap_desc or "",
        ))

    # --- No fuel ---
    no_fuel = [e for e in entities if e.status == "NO_FUEL"]
    if no_fuel:
        causes.append(RootCause(
            failure_type     = FailureType.NO_FUEL,
            description      = "Burner entities have run out of fuel.",
            affected         = no_fuel,
            downstream_effects = ["machines halted until refueled"],
            evidence         = f"{len(no_fuel)} entity/entities out of fuel",
        ))

    # --- Output blocked (drills waiting for space) ---
    # WAITING_FOR_SPACE_IN_DESTINATION on a drill is normal under load —
    # the output belt tile is momentarily full but the drill is still mining.
    # Only treat it as a real failure if ALL drills are blocked (full deadlock).
    all_drills     = [e for e in entities if "mining-drill" in e.name]
    blocked_drills = [e for e in all_drills
                      if e.status == "WAITING_FOR_SPACE_IN_DESTINATION"]
    has_power_gap  = any(c.failure_type == FailureType.POWER_NETWORK_GAP for c in causes)

    if blocked_drills and has_power_gap:
        # Fold into power gap effects — not a separate cause
        for c in causes:
            if c.failure_type == FailureType.POWER_NETWORK_GAP:
                c.downstream_effects.append(
                    f"{len(blocked_drills)} drill(s) blocked "
                    f"(output belt not being cleared — inserters have no power)"
                )
    elif len(blocked_drills) == len(all_drills) and all_drills:
        # Full deadlock — every drill is stuck, this is a real problem
        causes.append(RootCause(
            failure_type       = FailureType.OUTPUT_BLOCKED,
            description        = "All mining drills are blocked — output belt fully congested.",
            affected           = blocked_drills,
            downstream_effects = ["zero ore output", "all furnaces will starve"],
            evidence           = f"{len(blocked_drills)}/{len(all_drills)} drills blocked",
        ))
    # else: partial congestion — normal under load, not a root cause

    # --- Starved furnaces ---
    starved = [e for e in entities
               if ("furnace" in e.name or "assembling-machine" in e.name)
               and e.status == "NO_INGREDIENTS"]
    if starved:
        causes.append(RootCause(
            failure_type     = FailureType.NO_INGREDIENTS,
            description      = "Processors have fuel but no input items.",
            affected         = starved,
            downstream_effects = ["zero output", "iron plate production halted"],
            evidence         = f"{len(starved)} furnace(s) starved",
        ))

    # --- No production at all ---
    plate_rate = throughput.get("iron-plate", 0.0)
    if plate_rate == 0 and not starved and not no_fuel:
        smelting = systems.get(SubsystemName.SMELTING)
        if smelting and smelting.total_count == 0:
            causes.append(RootCause(
                failure_type     = FailureType.NOT_BUILT,
                description      = "No smelting infrastructure exists yet.",
                affected         = [],
                downstream_effects = ["no iron plate production possible"],
            ))

    # --- Update subsystem statuses ---
    for sys_name, sys in systems.items():
        if sys.total_count == 0:
            sys.status = "ABSENT"
        elif sys.operational_count == sys.total_count:
            sys.status = "OK"
        elif sys.operational_count > 0:
            sys.status = "DEGRADED"
        else:
            sys.status = "DOWN"

        sys.throughput = throughput.get("iron-plate", 0.0) \
            if sys_name == SubsystemName.SMELTING else 0.0

        # Bottlenecks per system
        sys.bottlenecks = [e for e in sys.entities if e.is_blocked]

    return causes


# ---------------------------------------------------------------------------
# Step 7 — generate_repairs
# ---------------------------------------------------------------------------

def _find_free_tile_between(
    pole: "SemanticEntity",
    consumer: "SemanticEntity",
    entities: list["SemanticEntity"],
    step: float = 5.0,
) -> Optional[tuple[float, float]]:
    """
    Walk from pole toward consumer, placing candidate positions every `step` tiles.
    At each candidate, try nearby offsets to find a tile not occupied by any
    known entity. Returns the first free tile found, or None.

    Uses a smaller step than pole wire reach (9 tiles) so we never overshoot.
    Also checks positions between pole and consumer when gap < step.
    """
    cx, cy = pole.x, pole.y
    tx, ty = consumer.x, consumer.y

    # Build occupied set from known entities (0.5-grid snapped)
    occupied: set[tuple[float, float]] = set()
    for e in entities:
        occupied.add((round(e.x * 2) / 2, round(e.y * 2) / 2))

    def try_position(sx: float, sy: float) -> Optional[tuple[float, float]]:
        """Try a snapped position and nearby offsets. Return first free tile."""
        candidates = [
            (sx, sy),
            (sx, sy - 1.0), (sx, sy + 1.0),
            (sx, sy - 2.0), (sx, sy + 2.0),
            (sx - 1.0, sy), (sx + 1.0, sy),
        ]
        for cx2, cy2 in candidates:
            if (cx2, cy2) not in occupied:
                return (cx2, cy2)
        return None

    # Walk from pole toward consumer in steps
    # If the gap is smaller than step, try midpoint and positions near consumer
    dx = tx - cx
    dy = ty - cy
    total_dist = (dx*dx + dy*dy) ** 0.5

    if total_dist < 0.5:
        return None  # Already at consumer

    # Generate candidate positions along the path
    num_steps = max(1, int(total_dist / step) + 1)
    for i in range(1, num_steps + 2):
        frac = min(i * step / total_dist, 1.0)
        nx = cx + dx * frac
        ny = cy + dy * frac
        sx = round(nx * 2) / 2
        sy = round(ny * 2) / 2
        result = try_position(sx, sy)
        if result:
            return result

    return None


def generate_repairs(
    causes: list[RootCause],
    entities: list[SemanticEntity],
    powered_ids: set[int],
    inventory: dict[str, int],
) -> list[RepairOpportunity]:
    """
    Step 7: Translate root causes into concrete repair opportunities.

    Ordered by impact (highest first).
    """
    opportunities: list[RepairOpportunity] = []
    poles_available = inventory.get("small-electric-pole", 0)

    for cause in causes:
        if cause.failure_type == FailureType.POWER_NETWORK_GAP:
            # Suggest position: midpoint between last connected pole and
            # first unpowered consumer
            poles    = [e for e in entities if "electric-pole" in e.name
                        and id(e) in powered_ids]
            no_power = cause.affected

            suggested = None
            if no_power:
                # Find the nearest pole to the first unpowered consumer.
                # We cannot use powered_ids here (it tracks consumers, not poles).
                # Instead: all poles are candidates; the nearest one to the
                # unpowered consumer is the "last connected pole".
                all_poles = [e for e in entities if "electric-pole" in e.name]
                uc = no_power[0]
                if all_poles:
                    lp = min(all_poles,
                             key=lambda p: _dist(p.x, p.y, uc.x, uc.y))
                    suggested = _find_free_tile_between(lp, uc, entities)

            # How many poles to bridge the gap?
            # Parse shortfall from gap_desc, fall back to 1 if unavailable.
            shortfall = 0.0
            if cause.evidence:
                import re
                m = re.search(r"([\d.]+) tiles short", cause.evidence)
                if m:
                    shortfall = float(m.group(1))
            pole_supply = POLE_SUPPLY_RADIUS.get("small-electric-pole", 7.5)
            if shortfall <= 0:
                needed = 1
            else:
                needed = max(1, int((shortfall / pole_supply) + 0.99))  # ceiling
            has_enough = poles_available >= needed

            opportunities.append(RepairOpportunity(
                repair_type      = RepairType.PLACE_POLE,
                description      = (
                    f"Extend power network to reach {len(no_power)} unpowered consumer(s). "
                    f"Place small-electric-pole(s) to bridge the gap. "
                    f"{cause.evidence}"
                ),
                impact           = "HIGH",
                cost             = "LOW" if has_enough else "MEDIUM",
                fixes            = [cause],
                suggested_position = suggested,
                required_items   = {"small-electric-pole": needed},
            ))

        elif cause.failure_type == FailureType.NO_FUEL:
            coal_available = inventory.get("coal", 0)
            opportunities.append(RepairOpportunity(
                repair_type  = RepairType.ADD_FUEL,
                description  = (
                    f"Refuel {len(cause.affected)} entity/entities with coal. "
                    f"Insert coal into each entity's fuel slot."
                ),
                impact       = "HIGH",
                cost         = "LOW" if coal_available >= len(cause.affected) else "MEDIUM",
                fixes        = [cause],
                required_items = {"coal": len(cause.affected)},
            ))

        elif cause.failure_type == FailureType.OUTPUT_BLOCKED:
            opportunities.append(RepairOpportunity(
                repair_type  = RepairType.CLEAR_OUTPUT,
                description  = (
                    f"Unblock output of {len(cause.affected)} drill(s). "
                    f"Check the output tile — place a belt or chest to receive ore."
                ),
                impact       = "MEDIUM",
                cost         = "LOW",
                fixes        = [cause],
            ))

        elif cause.failure_type == FailureType.NO_INGREDIENTS:
            opportunities.append(RepairOpportunity(
                repair_type  = RepairType.ADD_ORE,
                description  = (
                    f"{len(cause.affected)} furnace(s) starved. "
                    f"Once power is restored, inserters should resume feeding ore. "
                    f"Verify ore is present on the input belt."
                ),
                impact       = "HIGH",
                cost         = "LOW",
                fixes        = [cause],
            ))

        elif cause.failure_type == FailureType.NOT_BUILT:
            opportunities.append(RepairOpportunity(
                repair_type  = RepairType.PLACE_FURNACE,
                description  = "Build initial smelting array: drill → inserter → furnace.",
                impact       = "HIGH",
                cost         = "HIGH",
                fixes        = [cause],
            ))

    # Sort: HIGH impact first
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    opportunities.sort(key=lambda o: order.get(o.impact, 9))
    return opportunities


# ---------------------------------------------------------------------------
# Step 8 — summarize (the text the LLM sees)
# ---------------------------------------------------------------------------

def summarize(
    systems: dict[SubsystemName, Subsystem],
    flows: list[ItemFlow],
    causes: list[RootCause],
    opportunities: list[RepairOpportunity],
    throughput: dict[str, float],
    inventory: dict[str, int],
    technologies: list[str],
    goal: str,
    current_requirement: Optional[str] = None,
) -> str:
    SEP = "=" * 50
    out: list[str] = []

    out.append("=== GOAL ===")
    out.append(goal)
    if current_requirement:
        out.append(f"\nCurrent requirement: {current_requirement}")
    out.append("")

    # --- Factory Summary ---
    out.append(SEP)
    out.append("=== FACTORY SUMMARY ===")
    out.append("")
    plate_rate = throughput.get("iron-plate", 0.0)
    out.append(f"Iron plate production: {plate_rate:.2f}/sec")
    out.append("")

    out.append("Subsystems:")
    for sys_name, sys in systems.items():
        if sys.status == "ABSENT":
            continue
        out.append(f"  [{sys.status:8s}] {sys_name.value}  "
                   f"({sys.operational_count}/{sys.total_count} operational)")
    out.append("")

    # --- Root Causes ---
    out.append(SEP)
    out.append("=== ROOT CAUSE ANALYSIS ===")
    out.append("")
    if not causes:
        out.append("No critical failures detected.")
    else:
        for i, c in enumerate(causes):
            label = "PRIMARY" if i == 0 else f"SECONDARY {i}"
            out.append(f"{label}: {c.failure_type.value}")
            out.append(f"  {c.description}")
            if c.evidence:
                out.append(f"  Evidence: {c.evidence}")
            out.append(f"  Affects: {len(c.affected)} entity/entities")
            if c.downstream_effects:
                out.append("  Effects:")
                for ef in c.downstream_effects:
                    out.append(f"    - {ef}")
            out.append("")

    # --- Item Flows ---
    broken_flows = [f for f in flows if f.is_broken]
    if flows:
        out.append(SEP)
        out.append(f"=== ITEM FLOWS ({len(flows)} total, {len(broken_flows)} broken) ===")
        out.append("")
        for i, flow in enumerate(flows, 1):
            status = "BROKEN" if flow.is_broken else "OK"
            fb = flow.first_broken
            out.append(f"Flow {i}: [{status}]")
            out.append(flow.summary())
            if fb:
                out.append(f"    → First break: {fb.label} — {fb.block_reason}")
            out.append("")

    # --- Repair Opportunities ---
    out.append(SEP)
    out.append("=== REPAIR OPPORTUNITIES ===")
    out.append("")
    for i, op in enumerate(opportunities, 1):
        out.append(f"Priority {i} [{op.impact} IMPACT / {op.cost} COST]: "
                   f"{op.repair_type.value}")
        out.append(f"  {op.description}")
        if op.suggested_position:
            x, y = op.suggested_position
            out.append(f"  Suggested position: ({x:.1f}, {y:.1f})")
        if op.required_items:
            items = ", ".join(f"{k}:{v}" for k, v in op.required_items.items())
            out.append(f"  Required items: {items}")
        out.append("")

    # --- Resources ---
    out.append(SEP)
    out.append("=== AVAILABLE RESOURCES ===")
    out.append("")
    for item, count in sorted(inventory.items()):
        out.append(f"  {item}: {count}")
    out.append("")

    # --- Technologies ---
    out.append(SEP)
    out.append("=== TECHNOLOGIES ===")
    out.append("")
    for t in technologies:
        out.append(f"  {t}")
    if not technologies:
        out.append("  none")
    out.append("")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def build_semantic_world_model(
    raw_entities: list[dict],
    throughput: dict[str, float],
    inventory: dict[str, int],
    technologies: list[str],
    goal: str,
    current_requirement: Optional[str] = None,
) -> str:
    """
    Full pipeline: raw entities → planner-ready text.

    Returns the serialized summary string for the LLM planner.
    Also returns internal objects for programmatic use if needed.
    """
    entities   = build_entities(raw_entities)
    build_relationships(entities)
    systems    = detect_subsystems(entities)
    powered_ids, gap_desc = trace_power(entities)
    flows      = trace_item_flows(entities)
    causes     = diagnose(entities, systems, powered_ids, gap_desc, flows, throughput)
    repairs    = generate_repairs(causes, entities, powered_ids, inventory)

    return summarize(
        systems=systems,
        flows=flows,
        causes=causes,
        opportunities=repairs,
        throughput=throughput,
        inventory=inventory,
        technologies=technologies,
        goal=goal,
        current_requirement=current_requirement,
    )


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    raw_entities = [
        # Power generation
        {"name": "offshore-pump",        "x": -40.5, "y": 22.5, "direction": 12, "status": "WORKING",       "fuel": {},           "input": {}, "output": {}, "products_finished": 0},
        {"name": "boiler",               "x": -37.5, "y": 23.0, "direction": 8,  "status": "FULL_OUTPUT",   "fuel": {"coal": 26}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "pipe",                 "x": -39.5, "y": 22.5, "direction": 0,  "status": "WORKING",       "fuel": {},           "input": {}, "output": {}, "products_finished": 0},
        {"name": "pipe",                 "x": -37.5, "y": 25.5, "direction": 0,  "status": "WORKING",       "fuel": {},           "input": {}, "output": {}, "products_finished": 0},
        {"name": "pipe",                 "x": -37.5, "y": 24.5, "direction": 0,  "status": "WORKING",       "fuel": {},           "input": {}, "output": {}, "products_finished": 0},
        {"name": "steam-engine",         "x": -34.5, "y": 25.5, "direction": 4,  "status": "WORKING",       "fuel": {},           "input": {}, "output": {}, "products_finished": 0},
        # Power poles — connected chain
        {"name": "small-electric-pole",  "x": -30.5, "y": 28.5, "direction": 0, "status": "CONNECTED", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "small-electric-pole",  "x": -23.5, "y": 28.5, "direction": 0, "status": "CONNECTED", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "small-electric-pole",  "x": -16.5, "y": 28.5, "direction": 0, "status": "CONNECTED", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "small-electric-pole",  "x":  -9.5, "y": 28.5, "direction": 0, "status": "CONNECTED", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "small-electric-pole",  "x":  -2.5, "y": 28.5, "direction": 0, "status": "CONNECTED", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "small-electric-pole",  "x":   4.5, "y": 28.5, "direction": 0, "status": "CONNECTED", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "small-electric-pole",  "x":  11.5, "y": 27.5, "direction": 0, "status": "CONNECTED", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "small-electric-pole",  "x":  18.5, "y": 26.5, "direction": 0, "status": "CONNECTED", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "small-electric-pole",  "x":  24.5, "y": 29.5, "direction": 0, "status": "CONNECTED", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "small-electric-pole",  "x":  30.5, "y": 33.5, "direction": 0, "status": "CONNECTED", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "small-electric-pole",  "x":  36.5, "y": 31.5, "direction": 0, "status": "CONNECTED", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "small-electric-pole",  "x":  39.5, "y": 27.5, "direction": 0, "status": "CONNECTED", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        # Poles near smelting array
        {"name": "small-electric-pole",  "x":  44.5, "y": 28.5, "direction": 0, "status": "CONNECTED", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "small-electric-pole",  "x":  44.5, "y": 31.5, "direction": 0, "status": "CONNECTED", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "small-electric-pole",  "x":  48.5, "y": 29.5, "direction": 0, "status": "CONNECTED", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "small-electric-pole",  "x":  44.5, "y": 35.5, "direction": 0, "status": "CONNECTED", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "small-electric-pole",  "x":  50.5, "y": 35.5, "direction": 0, "status": "CONNECTED", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "small-electric-pole",  "x":  57.5, "y": 35.5, "direction": 0, "status": "CONNECTED", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "small-electric-pole",  "x":  64.5, "y": 35.5, "direction": 0, "status": "CONNECTED", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        # Isolated pole — genuinely disconnected: nearest connected pole is
        # pole@(64.5,35.5) which is ~4.5 tiles away, within wire reach (9.0).
        # To create a real gap, we move it far enough that no connected pole
        # can reach it with a wire (>9 tiles from the nearest connected pole).
        {"name": "small-electric-pole",  "x":  75.5, "y": 33.5, "direction": 0, "status": "CONNECTED", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        # Smelting array
        {"name": "wooden-chest",  "x": 40.5, "y": 27.5, "direction": 0, "status": "NORMAL",         "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "inserter",      "x": 41.5, "y": 27.5, "direction": 4, "status": "NO_POWER",       "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "inserter",      "x": 43.5, "y": 28.5, "direction": 8, "status": "NO_POWER",       "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "stone-furnace", "x": 43.0, "y": 30.0, "direction": 0, "status": "NO_INGREDIENTS", "fuel": {"coal": 4}, "input": {}, "output": {"iron-plate": 22}, "products_finished": 766},
        {"name": "inserter",      "x": 43.5, "y": 31.5, "direction": 8, "status": "NO_POWER",       "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "inserter",      "x": 45.5, "y": 28.5, "direction": 8, "status": "NO_POWER",       "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "stone-furnace", "x": 45.0, "y": 30.0, "direction": 0, "status": "NO_INGREDIENTS", "fuel": {"coal": 5}, "input": {}, "output": {"iron-plate": 1},  "products_finished": 623},
        {"name": "inserter",      "x": 45.5, "y": 31.5, "direction": 8, "status": "NO_POWER",       "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "inserter",      "x": 47.5, "y": 28.5, "direction": 8, "status": "NO_POWER",       "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "stone-furnace", "x": 47.0, "y": 30.0, "direction": 0, "status": "NO_INGREDIENTS", "fuel": {"coal": 5}, "input": {}, "output": {"iron-plate": 1},  "products_finished": 803},
        {"name": "inserter",      "x": 47.5, "y": 31.5, "direction": 8, "status": "NO_POWER",       "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        # Mining drills
        {"name": "burner-mining-drill", "x": 45.0, "y": 34.0, "direction": 0,  "status": "WAITING_FOR_SPACE_IN_DESTINATION", "fuel": {"coal": 5}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "burner-mining-drill", "x": 48.0, "y": 34.0, "direction": 0,  "status": "WAITING_FOR_SPACE_IN_DESTINATION", "fuel": {"coal": 4}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "burner-mining-drill", "x": 51.0, "y": 34.0, "direction": 0,  "status": "WAITING_FOR_SPACE_IN_DESTINATION", "fuel": {"coal": 2}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "burner-mining-drill", "x": 70.0, "y": 32.0, "direction": 12, "status": "WAITING_FOR_SPACE_IN_DESTINATION", "fuel": {"coal": 5}, "input": {}, "output": {}, "products_finished": 0},
        # Inserters pulling ore from drills / feeding coal
        {"name": "inserter", "x": 46.5, "y": 33.5, "direction": 4,  "status": "NO_POWER", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "inserter", "x": 49.5, "y": 33.5, "direction": 4,  "status": "NO_POWER", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "inserter", "x": 52.5, "y": 33.5, "direction": 4,  "status": "NO_POWER", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
        {"name": "inserter", "x": 75.5, "y": 31.5, "direction": 12, "status": "NO_POWER", "fuel": {}, "input": {}, "output": {}, "products_finished": 0},
    ]

    result = build_semantic_world_model(
        raw_entities=raw_entities,
        throughput={"iron-plate": 0.0},
        inventory={
            "wooden-chest": 1, "inserter": 1, "small-electric-pole": 3,
            "wood": 25, "coal": 58, "stone": 11,
            "iron-ore": 3, "copper-ore": 1, "iron-plate": 120, "copper-plate": 86,
        },
        technologies=["electronics", "steam-power"],
        goal="Build fully automated iron plate production.",
        current_requirement="Restore iron ore flow and furnace throughput.",
    )

    print(result)