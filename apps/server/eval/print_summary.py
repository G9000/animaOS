"""
Print a comparison table across all result files in a directory.

Usage:
  python print_summary.py results/
"""

import json
import sys
from pathlib import Path


def main() -> None:
    results_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("results")
    if not results_dir.exists():
        print(f"Directory not found: {results_dir}")
        sys.exit(1)

    files = sorted(results_dir.glob("*.json"))
    if not files:
        print("No result files found.")
        sys.exit(1)

    rows: list[dict] = []
    all_cats: set[str] = set()

    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        summary = data.get("summary", {})
        benchmark = data.get("benchmark", "?")
        config = data.get("config", "?")
        variant = data.get("variant", "")

        label = f"{benchmark}"
        if variant:
            label += f"/{variant}"
        label += f" [{config}]"

        row = {
            "label": label,
            "total": summary.get("scored", summary.get("total", 0)),
            "correct": summary.get("correct", 0),
            "accuracy": summary.get("accuracy", 0),
        }

        by_cat = summary.get("by_category_scored", {})
        for cat, vals in by_cat.items():
            all_cats.add(cat)
            row[f"cat_{cat}"] = vals.get("accuracy", 0)

        rows.append(row)

    # Print table
    print(f"\n{'='*80}")
    print("AnimaOS Benchmark Results")
    print(f"{'='*80}\n")

    # Header
    header = f"{'Config':<35s} {'Total':>6s} {'Correct':>7s} {'Acc%':>6s}"
    print(header)
    print("-" * len(header))

    for row in rows:
        acc_str = f"{row['accuracy']*100:.1f}%" if row['total'] else "N/A"
        print(f"{row['label']:<35s} {row['total']:>6d} {row['correct']:>7d} {acc_str:>6s}")

    # Per-category breakdown
    if all_cats:
        print(f"\n{'Per-Category Accuracy':}")
        print("-" * 70)
        cats_sorted = sorted(all_cats)
        header2 = f"{'Config':<25s}" + "".join(f" {c[:12]:>12s}" for c in cats_sorted)
        print(header2)
        print("-" * len(header2))

        for row in rows:
            line = f"{row['label']:<25s}"
            for cat in cats_sorted:
                val = row.get(f"cat_{cat}")
                if val is not None:
                    line += f" {val*100:>11.1f}%"
                else:
                    line += f" {'—':>12s}"
            print(line)

    # Ablation comparison (if multiple configs for same benchmark)
    benchmarks: dict[str, list[dict]] = {}
    for row in rows:
        bm = row["label"].split(" [")[0]
        if bm not in benchmarks:
            benchmarks[bm] = []
        benchmarks[bm].append(row)

    for bm, bm_rows in benchmarks.items():
        if len(bm_rows) > 1:
            print(f"\n{'Ablation: ' + bm}")
            print("-" * 50)
            baseline = min(bm_rows, key=lambda r: r["accuracy"])
            for row in sorted(bm_rows, key=lambda r: r["accuracy"]):
                delta = (row["accuracy"] - baseline["accuracy"]) * 100
                delta_str = f"+{delta:.1f}%" if delta > 0 else f"{delta:.1f}%"
                print(
                    f"  {row['label']:<30s}  "
                    f"{row['accuracy']*100:5.1f}%  "
                    f"({delta_str} vs baseline)"
                )

    print()


if __name__ == "__main__":
    main()
