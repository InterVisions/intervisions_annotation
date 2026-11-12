#!/usr/bin/env python3
"""Stratified sample for the cross-annotation (inter-annotator agreement) study.

Unit of analysis: one annotated person. Only images with exactly one person (num_people = 1) are
in the study: in couple images and in multi-person images of single campaigns a blind re-annotator
cannot know which person the original label refers to. Every unit has person_idx 1.
Strata: prompt x FST group of the original label (1-2, 3-4, 5-6, 0 = cannot determine).

Default sizes (all configurable):
  FST 5-6: every available unit, unless the total exceeds --cap-56 (then spread evenly over prompts)
  FST 3-4: --n-34 units in total, spread evenly over prompts
  FST 1-2: --n-12 units in total, spread evenly over prompts
  FST 0:   same sampling rate as FST 1-2 in the same prompt
  then each prompt is topped up to --min-per-prompt people.

Within a stratum the selection is random (fixed --seed) and balanced between original annotators.
pi = selected / available in each stratum, and weight = 1 / pi.

Each unit goes to an annotator who did not annotate it (with two annotators, the other one).
--intra N adds N units per annotator from their own images (phase "intra").
--test-user U adds --test-n units for U (phase "test") to try the interface; the analysis ignores them.

Usage:
  python scripts/xa_sample.py --db /data/intervisions/intervisions.db -o sample.csv
  python scripts/xa_sample.py --csv intervisions_annotations.csv -o sample.csv
  python scripts/xa_sample.py --db ... --test-user lluis -o sample.csv
"""
import argparse
import csv
import random
import sqlite3
import sys
from collections import Counter, defaultdict

GROUPS = ["1-2", "3-4", "5-6", "0"]
OUT_COLUMNS = ["image_id", "person_idx", "prompt", "original_annotator", "reannotator",
               "stratum", "pi", "weight", "phase"]


def fst_group(v):
    if v == 0:
        return "0"
    return {1: "1-2", 2: "1-2", 3: "3-4", 4: "3-4", 5: "5-6", 6: "5-6"}.get(v)


def to_int(v):
    if v is None or v == "":
        return None
    return int(v)


def load_rows_db(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    rows = db.execute("""
        SELECT a.id AS annotation_id, u.username AS annotator, te.term, c.annotation_type, a.num_people,
               a.perceived_gender, a.perceived_skin_tone, a.p2_perceived_gender, a.p2_perceived_skin_tone
        FROM annotations a
        JOIN tasks t ON a.task_id = t.id JOIN users u ON t.annotator_id = u.id
        JOIN terms te ON t.term_id = te.id JOIN campaigns c ON te.campaign_id = c.id
    """).fetchall()
    return [dict(r) for r in rows]


def load_rows_csv(path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append({
                "annotation_id": r["annotation_id"], "annotator": r["annotator"], "term": r["term"],
                "annotation_type": r["annotation_type"], "num_people": r["num_people"],
                "perceived_gender": r["perceived_gender"], "perceived_skin_tone": r["perceived_skin_tone"],
                "p2_perceived_gender": r["p2_perceived_gender"],
                "p2_perceived_skin_tone": r["p2_perceived_skin_tone"],
            })
    return rows


def build_units(rows, annotators):
    """Person-level units. Returns (units, skipped counter)."""
    units, skipped = [], Counter()
    for r in rows:
        if r["annotator"] not in annotators:
            skipped["annotator not in study"] += 1
            continue
        if r["annotation_type"] == "couple":
            skipped["couple image (not in study)"] += 1
            continue
        if str(r["num_people"]) != "1":
            skipped[f"num_people = {r['num_people'] or 'empty'} (only one-person images)"] += 1
            continue
        g, st = to_int(r["perceived_gender"]), to_int(r["perceived_skin_tone"])
        if g is None or st is None or fst_group(st) is None:
            skipped["missing gender or skin tone"] += 1
            continue
        units.append({
            "image_id": int(r["annotation_id"]), "person_idx": 1, "prompt": r["term"],
            "original_annotator": r["annotator"], "group": fst_group(st),
        })
    units.sort(key=lambda u: u["image_id"])
    return units, skipped


def spread(total, capacity, rng):
    """Split `total` as evenly as possible over keys, without exceeding capacity[key]."""
    alloc = {k: 0 for k in capacity}
    order = sorted(capacity)
    rng.shuffle(order)  # which keys get the remainder is random but reproducible
    remaining = total
    while remaining > 0:
        progressed = False
        for k in order:
            if remaining and alloc[k] < capacity[k]:
                alloc[k] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break
    return alloc


def select_balanced(pool, k, rng):
    """Pick k units from pool, balanced between original annotators."""
    by_ann = defaultdict(list)
    for u in pool:
        by_ann[u["original_annotator"]].append(u)
    alloc = spread(k, {a: len(v) for a, v in by_ann.items()}, rng)
    chosen = []
    for a in sorted(by_ann):
        chosen.extend(rng.sample(by_ann[a], alloc[a]))
    return chosen


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--db", help="SQLite database of the platform")
    src.add_argument("--csv", help="CSV export of the platform (Export CSV)")
    ap.add_argument("--annotators", default="alexia,blanca",
                    help="comma-separated usernames taking part in the study, as original annotators and "
                         "re-annotators (default: alexia,blanca)")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--cap-56", type=int, default=50, help="global cap for FST 5-6 (default 50)")
    ap.add_argument("--n-34", type=int, default=65, help="total FST 3-4 units (default 65)")
    ap.add_argument("--n-12", type=int, default=65, help="total FST 1-2 units (default 65)")
    ap.add_argument("--min-per-prompt", type=int, default=10)
    ap.add_argument("--intra", type=int, default=0,
                    help="units per annotator from their own images for intra-annotator agreement (default 0)")
    ap.add_argument("--test-user", help="also add test units for this user (phase 'test', ignored by the analysis)")
    ap.add_argument("--test-n", type=int, default=20, help="number of test units (default 20)")
    ap.add_argument("-o", "--output", default="sample.csv")
    args = ap.parse_args()

    annotators = sorted(a.strip() for a in args.annotators.split(",") if a.strip())
    if len(annotators) < 2:
        sys.exit("need at least two annotators")
    rows = load_rows_db(args.db) if args.db else load_rows_csv(args.csv)
    units, skipped = build_units(rows, set(annotators))
    rng = random.Random(args.seed)

    strata = defaultdict(list)  # (prompt, group) -> units
    for u in units:
        strata[(u["prompt"], u["group"])].append(u)
    prompts = sorted({u["prompt"] for u in units})
    avail = {(p, g): len(strata[(p, g)]) for p in prompts for g in GROUPS}

    # ── Allocation per stratum ──
    n = {}
    total_56 = sum(avail[(p, "5-6")] for p in prompts)
    if total_56 <= args.cap_56:
        n.update({(p, "5-6"): avail[(p, "5-6")] for p in prompts})
    else:
        a = spread(args.cap_56, {p: avail[(p, "5-6")] for p in prompts}, rng)
        n.update({(p, "5-6"): a[p] for p in prompts})
    for g, total in (("3-4", args.n_34), ("1-2", args.n_12)):
        a = spread(total, {p: avail[(p, g)] for p in prompts}, rng)
        n.update({(p, g): a[p] for p in prompts})
    sum12 = sum(avail[(p, "1-2")] for p in prompts)
    global_rate12 = sum(n[(p, "1-2")] for p in prompts) / sum12 if sum12 else 0
    for p in prompts:
        rate = n[(p, "1-2")] / avail[(p, "1-2")] if avail[(p, "1-2")] else global_rate12
        n[(p, "0")] = min(avail[(p, "0")], round(avail[(p, "0")] * rate))
    # Minimum per prompt: top up the stratum with the most unselected units
    for p in prompts:
        while sum(n[(p, g)] for g in GROUPS) < args.min_per_prompt:
            spare = [(avail[(p, g)] - n[(p, g)], g) for g in GROUPS if avail[(p, g)] > n[(p, g)]]
            if not spare:
                break
            n[(p, max(spare)[1])] += 1

    # ── Selection ──
    selected = set()
    for key in sorted(strata):
        for u in select_balanced(strata[key], n[key], rng):
            selected.add((u["image_id"], u["person_idx"]))
    sample = [u for u in units if (u["image_id"], u["person_idx"]) in selected]

    # ── pi and weights: selected / available per stratum ──
    sel_count = Counter((u["prompt"], u["group"]) for u in sample)
    for u in sample:
        key = (u["prompt"], u["group"])
        u["pi"] = sel_count[key] / avail[key]
        u["weight"] = 1 / u["pi"]
        u["phase"] = "inter"

    # ── Re-annotator: someone other than the original annotator (least loaded if there are several) ──
    load = Counter()
    order = list(sample)
    rng.shuffle(order)
    for u in order:
        others = [a for a in annotators if a != u["original_annotator"]]
        rng.shuffle(others)
        u["reannotator"] = min(others, key=lambda a: load[a])
        load[u["reannotator"]] += 1

    # ── Optional intra-annotator phase ──
    intra = []
    if args.intra > 0:
        for a in annotators:
            own = [u for u in units if u["original_annotator"] == a]
            picked = rng.sample(own, min(args.intra, len(own)))
            pi = len(picked) / len(own) if own else 0
            for u in picked:
                intra.append(dict(u, reannotator=a, phase="intra", pi=pi, weight=1 / pi))

    # ── Optional test units (separate RNG, so the study sample does not depend on them) ──
    test = []
    if args.test_user:
        trng = random.Random(f"test-{args.seed}")
        pool = [u for u in units if u["original_annotator"] != args.test_user]
        for u in trng.sample(pool, min(args.test_n, len(pool))):
            test.append(dict(u, reannotator=args.test_user, phase="test", pi=1.0, weight=1.0))

    # ── Write ──
    out = sample + intra + test
    for u in out:
        assert u["phase"] == "intra" or u["reannotator"] != u["original_annotator"]
        assert u["phase"] == "test" or u["reannotator"] in annotators
    with open(args.output, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(OUT_COLUMNS)
        for u in sorted(out, key=lambda u: (u["phase"], u["image_id"], u["person_idx"])):
            w.writerow([u["image_id"], u["person_idx"], u["prompt"], u["original_annotator"],
                        u["reannotator"], f'{u["prompt"]}|FST {u["group"]}',
                        f'{u["pi"]:.6f}', f'{u["weight"]:.6f}', u["phase"]])

    # ── Summary ──
    print(f"seed={args.seed}  population={len(units)} units", file=sys.stderr)
    for reason, c in sorted(skipped.items()):
        print(f"  skipped: {c} ({reason})", file=sys.stderr)
    print(f"sample: {len(sample)} units" + (f" + {len(intra)} intra units" if intra else "")
          + (f" + {len(test)} test units for {args.test_user}" if test else ""), file=sys.stderr)
    print(f"\n{'prompt':55s} " + " ".join(f"{'FST ' + g:>12s}" for g in GROUPS) + f" {'total':>6s}",
          file=sys.stderr)
    for p in prompts:
        cells = " ".join(f"{sel_count[(p, g)]:>5d}/{avail[(p, g)]:<6d}" for g in GROUPS)
        print(f"{p[:55]:55s} {cells} {sum(sel_count[(p, g)] for g in GROUPS):>6d}", file=sys.stderr)
    print("\nselected/available per stratum. Units per (original -> reannotator):", file=sys.stderr)
    for (o, r), c in sorted(Counter((u["original_annotator"], u["reannotator"]) for u in sample).items()):
        print(f"  {o} -> {r}: {c}", file=sys.stderr)
    print(f"\nwrote {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
