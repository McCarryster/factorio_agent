"""
Diagnostic add-on for RewardCalculator.

Prints exactly which items/entities changed between snapshots and what each
contributed to the reward, so you can see why a "trivial" action gave +N.

Usage: replace your import in the agent loop temporarily:

    from metrics.reward_diagnose import DiagnosticRewardCalculator as RewardCalculator

The interface is identical (.reset(), .get_reward()) but each get_reward()
also prints a per-item breakdown of what changed.
"""

from metrics.reward_t import RewardCalculator
import math


class DiagnosticRewardCalculator(RewardCalculator):
    def __init__(self, client):
        super().__init__(client)
        self._prev_inv: dict = {}
        self._prev_world: dict = {}

    def reset(self) -> None:
        inv, world = self._snapshot()
        self.prev_total_v = self._total_v(inv, world)
        self._prev_inv = dict(inv)
        self._prev_world = dict(world)
        print(f"\n[reward.reset] baseline V_total = {self.prev_total_v:.3f}")
        print(f"[reward.reset] inv  ({sum(inv.values())} items, {len(inv)} types)")
        print(f"[reward.reset] world({sum(world.values())} entities, {len(world)} types)")

    def get_reward(self) -> float:
        inv, world = self._snapshot()
        current_v = self._total_v(inv, world)

        if self.prev_total_v is None:
            self.prev_total_v = current_v
            self._prev_inv = dict(inv)
            self._prev_world = dict(world)
            return 0.0

        reward = current_v - self.prev_total_v

        # Diff inventory and world to find sources of the delta
        all_inv_keys = set(inv) | set(self._prev_inv)
        all_world_keys = set(world) | set(self._prev_world)

        inv_changes = []
        for k in all_inv_keys:
            d = inv.get(k, 0) - self._prev_inv.get(k, 0)
            if d != 0:
                v = self.V(k)
                contrib = v * d if math.isfinite(v) else 0.0
                inv_changes.append((k, d, v, contrib))

        world_changes = []
        for k in all_world_keys:
            d = world.get(k, 0) - self._prev_world.get(k, 0)
            if d != 0:
                item = self.entity_to_item.get(k, k)
                v = self.V(item)
                contrib = v * d if math.isfinite(v) else 0.0
                world_changes.append((k, d, v, contrib))

        print(f"\n[reward] step delta = {reward:+.3f}  (V {self.prev_total_v:.3f} -> {current_v:.3f})")
        if inv_changes:
            print("  inventory changes:")
            inv_changes.sort(key=lambda x: -abs(x[3]))
            for name, delta, v, contrib in inv_changes:
                sign = "+" if delta > 0 else ""
                print(f"    {name:<35} {sign}{delta:<6} x V={v:7.3f}  -> {contrib:+8.3f}")
        if world_changes:
            print("  world (entity) changes:")
            world_changes.sort(key=lambda x: -abs(x[3]))
            for name, delta, v, contrib in world_changes:
                sign = "+" if delta > 0 else ""
                print(f"    {name:<35} {sign}{delta:<6} x V={v:7.3f}  -> {contrib:+8.3f}")
        if not inv_changes and not world_changes:
            print("  (no changes)")
        # sanity check
        accounted = sum(c for _, _, _, c in inv_changes) + sum(c for _, _, _, c in world_changes)
        if abs(accounted - reward) > 0.01:
            print(f"  WARNING: accounted={accounted:.3f} != reward={reward:.3f}, diff={reward - accounted:.3f}")

        self.prev_total_v = current_v
        self._prev_inv = dict(inv)
        self._prev_world = dict(world)
        return reward