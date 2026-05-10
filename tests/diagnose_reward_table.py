"""
Diagnostic: dump every item's V value and the recipe that produced the min.

Run from project root:
    python -m metrics.diagnose_v
"""

import math
from game_integration.factorio_bridge import get_client
from metrics.reward_t import RewardCalculator, alpha, RAW_VALUES, DEFAULT_CRAFTER_POWER, ENERGY_SCALE


def explain_v(calc: RewardCalculator, item: str) -> dict:
    """Compute V(item) but return which recipe won and its breakdown."""
    if item in RAW_VALUES:
        return {"item": item, "v": RAW_VALUES[item], "source": "RAW", "recipe": None}

    recipe_names = calc.item_to_recipes.get(item, [])
    if not recipe_names:
        return {"item": item, "v": 1.0, "source": "FALLBACK (no recipe)", "recipe": None}

    best = math.inf
    best_info = None
    for r_name in recipe_names:
        r = calc.recipes[r_name]
        ingredients = r["ingredients"]

        output_amount = 0.0
        for prod in r["products"]:
            if prod["name"] == item:
                output_amount += prod["amount"]
        if output_amount <= 0:
            continue

        ingredient_cost = sum(
            calc.V(ing["name"]) * ing["amount"] for ing in ingredients
        )
        if not math.isfinite(ingredient_cost):
            continue

        e_cost = DEFAULT_CRAFTER_POWER * (r["energy"] or 0.5) * ENERGY_SCALE
        a = alpha(len(ingredients))
        total = (ingredient_cost * a + e_cost) / output_amount

        if total < best:
            best = total
            best_info = {
                "recipe_name": r_name,
                "category": r.get("category"),
                "ingredient_cost": ingredient_cost,
                "n_ingredients": len(ingredients),
                "alpha": a,
                "energy_cost": e_cost,
                "output_amount": output_amount,
                "total": total,
                "ingredients": ingredients,
                "products": r["products"],
            }

    return {"item": item, "v": best, "source": "RECIPE", "recipe": best_info}


def main():
    client = get_client()
    print("Loading reward calculator...")
    calc = RewardCalculator(client)
    print(f"Loaded {len(calc.recipes)} recipes, {len(calc.entity_to_item)} entity mappings\n")

    # ---------- Section 1: V distribution ----------
    print("=" * 70)
    print("V DISTRIBUTION (sorted descending)")
    print("=" * 70)
    sorted_v = sorted(
        ((item, calc.V(item)) for item in calc.item_to_recipes),
        key=lambda x: -x[1] if math.isfinite(x[1]) else float("inf"),
    )
    print(f"{'Item':<40} {'V':>10}")
    print("-" * 52)
    # show top 30 highest, then bottom 30
    for item, v in sorted_v[:30]:
        print(f"{item:<40} {v:>10.3f}")
    print(f"\n... ({len(sorted_v) - 60} items omitted) ...\n")
    for item, v in sorted_v[-30:]:
        print(f"{item:<40} {v:>10.3f}")

    # ---------- Section 2: items at exactly 1.0 (the fallback default) ----------
    print("\n" + "=" * 70)
    print("ITEMS WITH V = 1.0 (suspect: hit fallback or have no usable recipe)")
    print("=" * 70)
    suspects = [item for item, v in sorted_v if abs(v - 1.0) < 1e-9 and item not in RAW_VALUES]
    print(f"Found {len(suspects)} items at exactly 1.0:")
    for item in suspects[:30]:
        recipes = calc.item_to_recipes.get(item, [])
        print(f"  {item:<40} recipes_indexed={len(recipes)}")
    if len(suspects) > 30:
        print(f"  ... and {len(suspects) - 30} more")

    # ---------- Section 3: detailed breakdown for problem items ----------
    print("\n" + "=" * 70)
    print("DETAILED BREAKDOWN OF PROBLEM ITEMS")
    print("=" * 70)
    for item in [
        "stone-furnace", "iron-gear-wheel", "inserter", "assembling-machine-1",
        "transport-belt", "electronic-circuit", "iron-plate", "copper-cable",
        "wooden-chest", "iron-chest", "burner-mining-drill",
    ]:
        info = explain_v(calc, item)
        print(f"\n--- {item} → V={info['v']:.4f} ({info['source']}) ---")
        if info["recipe"]:
            r = info["recipe"]
            print(f"  chosen recipe: {r['recipe_name']!r}  category={r['category']!r}")
            print(f"  output_amount = {r['output_amount']}  (recipe yields this many of {item})")
            print(f"  alpha({r['n_ingredients']}) = {r['alpha']}")
            print(f"  ingredient_cost (sum V*c) = {r['ingredient_cost']:.4f}")
            print(f"  energy_cost = {r['energy_cost']:.4f}")
            print(f"  formula: ({r['ingredient_cost']:.3f} * {r['alpha']:.2f} + {r['energy_cost']:.4f}) / {r['output_amount']} = {r['total']:.4f}")
            print(f"  ingredients: {r['ingredients']}")
            print(f"  products:    {r['products']}")
            # also show all alternative recipes
            all_r = calc.item_to_recipes.get(item, [])
            if len(all_r) > 1:
                print(f"  ({len(all_r)} candidate recipes total: {all_r})")


if __name__ == "__main__":
    main()