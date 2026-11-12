#!/usr/bin/env python3
"""Inter-annotator agreement report for the cross-annotation study.

Input: the export from Admin > Cross-annotation > Export (one row per unit with sample info,
original labels and new labels). Original and new labels are matched on image_id + person_idx.

Metrics:
  gender: Cohen's kappa, percent agreement
  FST, age: quadratic weighted kappa, ordinal Krippendorff's alpha, percent agreement
Scopes:
  overall_weighted   all inter units, weighted by w = 1/pi (weighted confusion matrix)
  overall_unweighted all inter units, unweighted (reference)
  fst_group          per FST group of the original label, unweighted
  stratum            per prompt x FST group, unweighted
  intra              per annotator, unweighted (only if the export has intra units)
"Cannot determine" (CD) handling:
  cd_category  CD is a regular category. For the ordinal metrics CD is at distance 1 (the maximum)
               from every other category; ordinal distances are scaled to [0, 1].
  cd_excluded  units where either annotation is CD are dropped
95% CIs: percentile, stratified bootstrap (resampling units within each stratum).

Usage:
  python scripts/xa_analysis.py intervisions_xa_annotations.csv -o xa_report [--iterations 2000 --seed 2026]
Requires numpy and matplotlib (scripts/requirements-analysis.txt).
"""
import argparse
import csv
import os
import sys
from collections import defaultdict

import numpy as np

AGE_LABELS = ["Child (0-12)", "Adolescent (13-17)", "Young adult (18-30)",
              "Middle-aged (31-60)", "Older adult (60+)"]
AGE_CD = "Cannot determine"
GROUPS = ["1-2", "3-4", "5-6", "0"]

# Each attribute: category labels (CD last), ordinal or not, and a function mapping a raw value to an index.
# Index len(labels)-1 is CD; None means missing.
def _gender(v):
    v = _int(v)
    return {0: 0, 1: 1, 2: 2, -1: 3}.get(v)

def _fst(v):
    v = _int(v)
    return None if v is None or not 0 <= v <= 6 else (6 if v == 0 else v - 1)

def _age(v):
    if v == AGE_CD:
        return 5
    return AGE_LABELS.index(v) if v in AGE_LABELS else None

def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

ATTRS = {
    "gender": {"labels": ["More maleness", "Androgynous / unclear", "More femaleness", "Cannot determine"],
               "short": ["M", "A/U", "F", "CD"], "ordinal": False, "code": _gender,
               "metrics": ["kappa", "agreement"]},
    "fst": {"labels": ["FST 1", "FST 2", "FST 3", "FST 4", "FST 5", "FST 6", "Cannot determine"],
            "short": ["1", "2", "3", "4", "5", "6", "CD"], "ordinal": True, "code": _fst,
            "metrics": ["kappa_quadratic", "alpha_ordinal", "agreement"]},
    "age": {"labels": AGE_LABELS + [AGE_CD],
            "short": ["Child", "Adol.", "Young", "Middle", "Older", "CD"], "ordinal": True, "code": _age,
            "metrics": ["kappa_quadratic", "alpha_ordinal", "agreement"]},
}
CD_MODES = ["cd_category", "cd_excluded"]


# ─── Metrics from a confusion matrix O (rows = original, cols = new) ──────────

def kappa_weights(K, ordinal):
    """Disagreement weights. Nominal: 0/1. Ordinal: quadratic on the ordinal categories,
    CD (last index) at distance 1 from everything else."""
    if not ordinal:
        return 1.0 - np.eye(K)
    m = K - 1
    i = np.arange(m)
    W = np.ones((K, K))
    W[:m, :m] = ((i[:, None] - i[None, :]) / (m - 1)) ** 2
    W[m, m] = 0.0
    return W

def kappa(O, W):
    n = O.sum()
    if n <= 0:
        return np.nan
    E = np.outer(O.sum(1), O.sum(0)) / n
    de = (W * E).sum()
    return np.nan if de <= 0 else 1.0 - (W * O).sum() / de

def agreement(O):
    n = O.sum()
    return np.nan if n <= 0 else np.trace(O) / n

def alpha_ordinal(O):
    """Krippendorff's alpha, ordinal metric, two coders, no missing values.
    The last category (CD) is at distance 1 from the others; ordinal distances are scaled to [0, 1]."""
    C = O + O.T
    nc = C.sum(1)
    n = nc.sum()
    if n <= 1:
        return np.nan
    m = len(nc) - 1
    cum = np.concatenate([[0.0], np.cumsum(nc[:m])])
    D = np.ones_like(C, dtype=float)
    for c in range(m):
        for k in range(m):
            lo, hi = min(c, k), max(c, k)
            D[c, k] = (cum[hi + 1] - cum[lo] - (nc[lo] + nc[hi]) / 2) ** 2
    mx = D[:m, :m].max()
    if mx > 0:
        D[:m, :m] /= mx
    D[m, m] = 0.0  # without CD values the scaling cancels out: ordinary ordinal alpha
    do = (C * D).sum() / n
    de = (np.outer(nc, nc) * D).sum() / (n * (n - 1))
    return np.nan if de <= 0 else 1.0 - do / de

def metric_values(O, attr):
    a = ATTRS[attr]
    out = {}
    for m in a["metrics"]:
        if m == "kappa":
            out[m] = kappa(O, kappa_weights(O.shape[0], False))
        elif m == "kappa_quadratic":
            out[m] = kappa(O, kappa_weights(O.shape[0], True))
        elif m == "alpha_ordinal":
            out[m] = alpha_ordinal(O)
        elif m == "agreement":
            out[m] = agreement(O)
    return out


# ─── Data ─────────────────────────────────────────────────────────────────────

def load(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    done = [r for r in rows if r.get("new_gender") not in (None, "")]
    return rows, done

def fst_group_of(stratum):
    return stratum.rsplit("|FST ", 1)[-1]


class Engine:
    """Bootstraps confusion matrices per resampling cell; every reported subset is a union of cells."""

    def __init__(self, rows, cell_of, weighted):
        self.rows = rows
        self.cells = sorted({cell_of(r) for r in rows})
        cidx = {c: i for i, c in enumerate(self.cells)}
        self.cell = np.array([cidx[cell_of(r)] for r in rows])
        w = np.array([float(r["weight"]) for r in rows]) if weighted else np.ones(len(rows))
        self.w = w * len(rows) / w.sum()  # normalised to mean 1 (needed for alpha's n(n-1) term)
        self.members = [np.flatnonzero(self.cell == i) for i in range(len(self.cells))]
        self.codes = {}
        for attr, a in ATTRS.items():
            K = len(a["labels"])
            col = "skin_tone" if attr == "fst" else attr
            o = [a["code"](r["orig_" + col]) for r in rows]
            n = [a["code"](r["new_" + col]) for r in rows]
            valid = np.array([x is not None and y is not None for x, y in zip(o, n)])
            oi = np.array([x if x is not None else 0 for x in o], dtype=int)
            ni = np.array([y if y is not None else 0 for y in n], dtype=int)
            for mode in CD_MODES:
                v = valid.copy()
                if mode == "cd_excluded":
                    v &= (oi != K - 1) & (ni != K - 1)
                self.codes[(attr, mode)] = (oi, ni, v, K)

    def matrices(self, idx, attr, mode, weighted):
        """Per-cell confusion matrices for the resampled unit indices idx -> array (cells, K, K)."""
        oi, ni, v, K = self.codes[(attr, mode)]
        idx = idx[v[idx]]
        flat = self.cell[idx] * K * K + oi[idx] * K + ni[idx]
        wts = self.w[idx] if weighted else None
        return np.bincount(flat, weights=wts, minlength=len(self.cells) * K * K).reshape(len(self.cells), K, K)

    def resample(self, rng):
        return np.concatenate([rng.choice(m, size=len(m), replace=True) for m in self.members if len(m)])

    def run(self, subsets, iterations, rng):
        """subsets: list of (scope, name, cell_names, weighted). Returns rows of results and point matrices."""
        cidx = {c: i for i, c in enumerate(self.cells)}
        subs = [(s, n, np.array([cidx[c] for c in cells if c in cidx], dtype=int), wt) for s, n, cells, wt in subsets]
        full = np.arange(len(self.rows))
        point, boot, mats = {}, defaultdict(list), {}
        for attr in ATTRS:
            for mode in CD_MODES:
                counts = self.matrices(full, attr, mode, False)
                weighted_m = self.matrices(full, attr, mode, True)
                for s, n, ci, wt in subs:
                    O = (weighted_m if wt else counts)[ci].sum(0)
                    mats[(s, n, attr, mode)] = O
                    point[(s, n, attr, mode)] = (metric_values(O, attr), int(counts[ci].sum()))
        for _ in range(iterations):
            idx = self.resample(rng)
            for attr in ATTRS:
                for mode in CD_MODES:
                    cache = {}
                    for s, n, ci, wt in subs:
                        if wt not in cache:
                            cache[wt] = self.matrices(idx, attr, mode, wt)
                        boot[(s, n, attr, mode)].append(metric_values(cache[wt][ci].sum(0), attr))
        results = []
        for key, (vals, n_units) in point.items():
            s, n, attr, mode = key
            for m, est in vals.items():
                bs = np.array([b[m] for b in boot[key]], dtype=float)
                bs = bs[~np.isnan(bs)]
                lo, hi = (np.percentile(bs, [2.5, 97.5]) if len(bs) else (np.nan, np.nan))
                results.append({"scope": s, "subset": n, "attribute": attr, "cd_mode": mode, "metric": m,
                                "estimate": est, "ci_low": lo, "ci_high": hi, "n_units": n_units,
                                "n_boot_valid": len(bs)})
        return results, mats


# ─── Outputs ──────────────────────────────────────────────────────────────────

def write_csv(path, rows, cols):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: (f"{r[c]:.4f}" if isinstance(r[c], float) else r[c]) for c in cols})

def write_confusion(path, O, attr):
    labels = ATTRS[attr]["labels"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["original \\ new"] + labels)
        for lab, row in zip(labels, O):
            w.writerow([lab] + [f"{x:.2f}" if not float(x).is_integer() else int(x) for x in row])

def plot_confusions(path, panels, attr):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    short = ATTRS[attr]["short"]
    fig, axes = plt.subplots(1, len(panels), figsize=(3.2 * len(panels), 3.4), squeeze=False)
    for ax, (title, O) in zip(axes[0], panels):
        rs = O.sum(1, keepdims=True)
        P = np.divide(O, rs, out=np.zeros_like(O, dtype=float), where=rs > 0)
        ax.imshow(P, cmap="Blues", vmin=0, vmax=1)
        for i in range(O.shape[0]):
            for j in range(O.shape[1]):
                if O[i, j]:
                    txt = f"{O[i, j]:.0f}" if float(O[i, j]).is_integer() else f"{O[i, j]:.1f}"
                    ax.text(j, i, txt, ha="center", va="center", fontsize=7,
                            color="white" if P[i, j] > 0.6 else "black")
        ax.set_xticks(range(len(short)), short, fontsize=7, rotation=45)
        ax.set_yticks(range(len(short)), short, fontsize=7)
        ax.set_xlabel("re-annotation", fontsize=8)
        ax.set_ylabel("original", fontsize=8)
        ax.set_title(title, fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)

def cd_usage(rows):
    """CD frequency per annotator, as original annotator and as re-annotator."""
    out = []
    for role, col_prefix, who in (("original", "orig_", "original_annotator"), ("reannotator", "new_", "reannotator")):
        by = defaultdict(list)
        for r in rows:
            by[r[who]].append(r)
        for ann, rs in sorted(by.items()):
            rec = {"annotator": ann, "role": role, "units": len(rs)}
            for attr in ATTRS:
                col = col_prefix + ("skin_tone" if attr == "fst" else attr)
                codes = [ATTRS[attr]["code"](r[col]) for r in rs]
                cd = sum(1 for c in codes if c == len(ATTRS[attr]["labels"]) - 1)
                rec[f"{attr}_cd"] = cd
                rec[f"{attr}_cd_pct"] = 100.0 * cd / len(rs) if rs else np.nan
                rec[f"{attr}_missing"] = sum(1 for c in codes if c is None)
            out.append(rec)
    return out

def fmt(r):
    if np.isnan(r["estimate"]):
        return "n/a"
    ci = "" if np.isnan(r["ci_low"]) else f" [{r['ci_low']:.2f}, {r['ci_high']:.2f}]"
    return f"{r['estimate']:.2f}{ci}"

def write_report(path, results, n_done, n_total, n_intra, args):
    idx = {(r["scope"], r["subset"], r["attribute"], r["cd_mode"], r["metric"]): r for r in results}
    lines = ["# Cross-annotation agreement report", "",
             f"Inter units re-annotated: {n_done} of {n_total}. Intra units: {n_intra}.",
             f"95% CIs: stratified bootstrap within strata, {args.iterations} iterations, seed {args.seed}.", ""]
    for mode in CD_MODES:
        lines += [f"## {'Cannot determine as a category' if mode == 'cd_category' else 'Cannot determine excluded'}", ""]
        scopes = [("overall_weighted", "all", "Overall (weighted by 1/π)"),
                  ("overall_unweighted", "all", "Overall (unweighted)")] + \
                 [("fst_group", g, f"FST group {g} (unweighted)") for g in GROUPS]
        for attr, a in ATTRS.items():
            lines += [f"### {attr}", "", "| Scope | n | " + " | ".join(a["metrics"]) + " |",
                      "|---|---|" + "---|" * len(a["metrics"])]
            for s, sub, label in scopes:
                rs = [idx.get((s, sub, attr, mode, m)) for m in a["metrics"]]
                if rs[0] is None:
                    continue
                lines.append(f"| {label} | {rs[0]['n_units']} | " + " | ".join(fmt(r) for r in rs) + " |")
            lines.append("")
    if n_intra:
        lines += ["## Intra-annotator (unweighted, CD as a category)", ""]
        for attr, a in ATTRS.items():
            lines += [f"### {attr}", "", "| Annotator | n | " + " | ".join(a["metrics"]) + " |",
                      "|---|---|" + "---|" * len(a["metrics"])]
            for r in sorted({r["subset"] for r in results if r["scope"] == "intra"}):
                rs = [idx.get(("intra", r, attr, "cd_category", m)) for m in a["metrics"]]
                lines.append(f"| {r} | {rs[0]['n_units']} | " + " | ".join(fmt(x) for x in rs) + " |")
            lines.append("")
    lines += ["Per-stratum results, confusion matrices and CD usage per annotator are in the CSV files "
              "of this folder.", ""]
    with open(path, "w") as f:
        f.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export_csv")
    ap.add_argument("-o", "--outdir", default="xa_report")
    ap.add_argument("--iterations", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    rows, done = load(args.export_csv)
    inter = [r for r in done if r.get("phase", "inter") == "inter"]
    intra = [r for r in done if r.get("phase") == "intra"]
    n_inter_total = sum(1 for r in rows if r.get("phase", "inter") == "inter")
    if not inter:
        sys.exit("no re-annotated inter units in the export")
    bad = [r for r in inter if r["original_annotator"] == r["reannotator"]]
    if bad:
        sys.exit(f"{len(bad)} inter units were re-annotated by their original annotator")
    print(f"inter: {len(inter)} of {n_inter_total} units re-annotated; intra: {len(intra)}", file=sys.stderr)

    rng = np.random.default_rng(args.seed)
    strata = sorted({r["stratum"] for r in inter})
    subsets = [("overall_weighted", "all", strata, True), ("overall_unweighted", "all", strata, False)]
    subsets += [("fst_group", g, [s for s in strata if fst_group_of(s) == g], False) for g in GROUPS
                if any(fst_group_of(s) == g for s in strata)]
    subsets += [("stratum", s, [s], False) for s in strata]
    results, mats = Engine(inter, lambda r: r["stratum"], weighted=True).run(subsets, args.iterations, rng)

    if intra:
        cell = lambda r: f'{r["reannotator"]}||{r["stratum"]}'
        cells = sorted({cell(r) for r in intra})
        annotators = sorted({r["reannotator"] for r in intra})
        isubs = [("intra", a, [c for c in cells if c.startswith(a + "||")], False) for a in annotators]
        ires, _ = Engine(intra, cell, weighted=False).run(isubs, args.iterations, rng)
        results += ires

    order = {"overall_weighted": 0, "overall_unweighted": 1, "fst_group": 2, "stratum": 3, "intra": 4}
    results.sort(key=lambda r: (order[r["scope"]], r["subset"], r["attribute"], r["cd_mode"], r["metric"]))
    write_csv(os.path.join(args.outdir, "metrics.csv"), results,
              ["scope", "subset", "attribute", "cd_mode", "metric", "estimate", "ci_low", "ci_high",
               "n_units", "n_boot_valid"])

    # Confusion matrices: overall (weighted and unweighted) and per FST group, both CD modes as CSV;
    # figures with CD as a category.
    cdir = os.path.join(args.outdir, "confusion")
    os.makedirs(cdir, exist_ok=True)
    for (s, sub, attr, mode), O in mats.items():
        if s == "stratum":
            continue
        name = s if sub == "all" else f"{s}_{sub}"
        write_confusion(os.path.join(cdir, f"{attr}_{mode}_{name}.csv"), O, attr)
    for attr in ATTRS:
        panels = [("Overall (weighted)", mats[("overall_weighted", "all", attr, "cd_category")])]
        panels += [(f"FST group {g}", mats[("fst_group", g, attr, "cd_category")]) for g in GROUPS
                   if ("fst_group", g, attr, "cd_category") in mats]
        plot_confusions(os.path.join(args.outdir, f"confusion_{attr}.png"), panels, attr)

    usage = cd_usage(inter)
    cols = ["annotator", "role", "units"] + [f"{a}_{k}" for a in ATTRS for k in ("cd", "cd_pct", "missing")]
    write_csv(os.path.join(args.outdir, "cd_usage.csv"), usage, cols)
    write_report(os.path.join(args.outdir, "report.md"), results, len(inter), n_inter_total, len(intra), args)
    print(f"wrote {args.outdir}/", file=sys.stderr)


if __name__ == "__main__":
    main()
