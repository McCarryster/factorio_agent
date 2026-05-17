# metrics/production_tracker.py

import time
from collections import defaultdict

class ProductionTracker:
    def __init__(self):
        # list of (timestamp, {entity_key: products_finished})
        self._history: list[tuple[float, dict]] = []

    def update(self, entities: list[dict]):
        snapshot = {}
        for e in entities:
            if e.get("products_finished", 0) > 0 or e["type"] in ("furnace", "assembling-machine"):
                key = f"{e['name']}@({e['x']},{e['y']})"
                snapshot[key] = e.get("products_finished", 0)
        self._history.append((time.time(), snapshot))
        # keep only last 5 minutes of history
        cutoff = time.time() - 300
        self._history = [(t, s) for t, s in self._history if t > cutoff]

    def get_throughput(self, window_seconds: int = 60) -> dict[str, float]:
        if len(self._history) < 2:
            return {}
        cutoff = time.time() - window_seconds
        recent = [(t, s) for t, s in self._history if t >= cutoff]
        if len(recent) < 2:
            recent = self._history[-2:]  # at minimum use last two snapshots
        first_t, first_s = recent[0]
        last_t, last_s = recent[-1]
        elapsed = last_t - first_t
        if elapsed < 0.1:
            return {}
        result = {}
        for key in last_s:
            if key in first_s:
                delta = last_s[key] - first_s[key]
                if delta > 0:
                    result[key] = round(delta / elapsed, 3)
        return result

    def format_throughput(self, entities: list[dict], window_seconds: int = 60) -> str:
        # show current totals always
        lines = [f"=== PRODUCTION METRICS ==="]
        
        producing = [(e, e["products_finished"]) for e in entities if e.get("products_finished", 0) > 0]
        if producing:
            for e, count in producing:
                lines.append(f"  {e['name']} at ({e['x']},{e['y']}): {count} total produced")
        
        # show rate if we have history
        throughput = self.get_throughput(window_seconds)
        if throughput:
            lines.append(f"  --- rates (last {window_seconds}s) ---")
            for key, rate in throughput.items():
                lines.append(f"  {key}: {rate}/sec")
        else:
            lines.append("  (rate tracking: need 2+ observations)")
        
        return "\n".join(lines)