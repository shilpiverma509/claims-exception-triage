"""Evaluation harness (T10): metrics, confusion matrix, prompt-version log.

Single run:
  python -m triage.evaluate --run outputs/triage_dev_claims_mock_v1.json --labels data/dev_labels.json

Prompt-version comparison table:
  python -m triage.evaluate --labels data/dev_labels.json \\
      --compare v1=outputs/triage_dev_claims_mock_v1.json \\
                v2=outputs/triage_dev_claims_mock_v2.json \\
                v3=outputs/triage_dev_claims_mock_v3.json

The sealed eval set (data/eval_claims.json / eval_labels.json) is touched
exactly once, after the prompt is frozen on the dev set — whatever this
prints for that run is recorded verbatim in AI_EVIDENCE.md (Ratified,
PRD-spec.md §6/§11).
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Optional

from triage.models import RootCause

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "outputs"

SEVERITY_CRITICAL_FLOOR = 4  # severity 4-5 counts as "critical" (PRD-spec.md §10)


def load_labels(path: Path) -> dict[str, dict]:
    return {row["claim_id"]: row for row in json.loads(path.read_text())}


def load_run(path: Path) -> list[dict]:
    """Apply the system's ranking policy (urgency desc, dollars break ties) so
    metrics measure the queue order an analyst would actually see."""
    results = json.loads(path.read_text())
    return sorted(results,
                  key=lambda r: (r["final_urgency"], r["claim"]["billed_amount_cents"]),
                  reverse=True)


def _scoreable(results: list[dict], labels: dict[str, dict]) -> list[tuple[dict, dict]]:
    """Pair each result with its label, skipping claims the run never scored
    (LLM failures, malformed records) or that aren't in the label set."""
    pairs = []
    for r in results:
        cid = r["claim"]["claim_id"]
        if cid in labels:
            pairs.append((r, labels[cid]))
    return pairs


def root_cause_accuracy(pairs: list[tuple[dict, dict]]) -> Optional[float]:
    if not pairs:
        return None
    correct = sum(1 for r, l in pairs if r["assessment"]["root_cause"] == l["root_cause"])
    return correct / len(pairs)


def routing_accuracy(pairs: list[tuple[dict, dict]]) -> Optional[float]:
    if not pairs:
        return None
    correct = sum(1 for r, l in pairs if r["final_queue"] == l["owner_queue"])
    return correct / len(pairs)


def human_review_rate(results: list[dict]) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if r["guard"]["forced_human_review"]) / len(results)


def critical_recall_at_n(results: list[dict], labels: dict[str, dict], n: int = 10,
                          severity_floor: int = SEVERITY_CRITICAL_FLOOR) -> Optional[float]:
    """% of ground-truth critical claims (severity >= floor) surfaced in the
    model's top-n by final_urgency. `results` must already be sorted desc."""
    critical_ids = {cid for cid, l in labels.items() if l["severity"] >= severity_floor}
    if not critical_ids:
        return None
    top_n_ids = {r["claim"]["claim_id"] for r in results[:n]}
    return len(critical_ids & top_n_ids) / len(critical_ids)


def spearman_urgency_correlation(pairs: list[tuple[dict, dict]]) -> Optional[float]:
    if len(pairs) < 2:
        return None
    from scipy.stats import spearmanr  # local import: only needed for eval, not the pipeline

    xs = [r["final_urgency"] for r, _ in pairs]
    ys = [l["severity"] for _, l in pairs]
    if len(set(xs)) == 1 or len(set(ys)) == 1:
        return None
    rho, _ = spearmanr(xs, ys)
    return float(rho)


def confusion_counts(pairs: list[tuple[dict, dict]]) -> Counter:
    """Counter keyed by (actual_root_cause, predicted_root_cause)."""
    counts: Counter = Counter()
    for r, l in pairs:
        counts[(l["root_cause"], r["assessment"]["root_cause"])] += 1
    return counts


def plot_confusion_matrix(counts: Counter, out_path: Path) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    causes = [c.value for c in RootCause]
    idx = {c: i for i, c in enumerate(causes)}
    n = len(causes)
    matrix = np.zeros((n, n), dtype=int)
    for (actual, predicted), count in counts.items():
        matrix[idx[actual]][idx[predicted]] = count

    fig, ax = plt.subplots(figsize=(8, 7))
    im = ax.imshow(matrix, cmap="Blues")
    ax.set_xticks(range(n)); ax.set_xticklabels(causes, rotation=45, ha="right")
    ax.set_yticks(range(n)); ax.set_yticklabels(causes)
    ax.set_xlabel("Predicted root cause"); ax.set_ylabel("Actual root cause")
    ax.set_title("Root-cause confusion matrix")
    vmax = matrix.max() if matrix.max() > 0 else 1
    for i in range(n):
        for j in range(n):
            val = matrix[i][j]
            if val:
                ax.text(j, i, str(val), ha="center", va="center",
                        color="white" if val > vmax / 2 else "black", fontsize=9)
    fig.colorbar(im, ax=ax, label="count")
    fig.tight_layout()
    out_path.parent.mkdir(exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def evaluate(run_path: Path, labels_path: Path, top_n: int = 10) -> dict:
    results = load_run(run_path)
    labels = load_labels(labels_path)
    pairs = _scoreable(results, labels)
    skipped = len(results) - len(pairs)

    metrics = {
        "run": str(run_path), "labels": str(labels_path),
        "n_results": len(results), "n_scored": len(pairs), "n_skipped": skipped,
        "root_cause_accuracy": root_cause_accuracy(pairs),
        "routing_accuracy": routing_accuracy(pairs),
        "human_review_rate": human_review_rate(results),
        f"critical_recall_at_{top_n}": critical_recall_at_n(results, labels, n=top_n),
        "spearman_urgency_correlation": spearman_urgency_correlation(pairs),
    }

    counts = confusion_counts(pairs)
    cm_path = OUT_DIR / f"confusion_matrix_{run_path.stem}.png"
    plot_confusion_matrix(counts, cm_path)
    metrics["confusion_matrix"] = str(cm_path)

    misses = [(r["claim"]["claim_id"], l["root_cause"], r["assessment"]["root_cause"])
              for r, l in pairs if r["assessment"]["root_cause"] != l["root_cause"]]

    report_path = OUT_DIR / f"eval_report_{run_path.stem}.md"
    report_path.write_text(_render_report(metrics, misses, cm_path))
    metrics["report"] = str(report_path)
    return metrics


def _render_report(metrics: dict, misses: list[tuple[str, str, str]], cm_path: Path) -> str:
    lines = [
        f"# Eval report — {metrics['run']}", "",
        f"Labels: `{metrics['labels']}` · scored {metrics['n_scored']}/{metrics['n_results']} "
        f"({metrics['n_skipped']} skipped: LLM failure or malformed record)", "",
        "| Metric | Value |", "|---|---|",
    ]
    for key in ("root_cause_accuracy", "routing_accuracy", "human_review_rate"):
        val = metrics[key]
        lines.append(f"| {key} | {f'{val:.1%}' if val is not None else 'n/a'} |")
    for key, val in metrics.items():
        if key.startswith("critical_recall_at_"):
            lines.append(f"| {key} | {f'{val:.1%}' if val is not None else 'n/a'} |")
    rho = metrics["spearman_urgency_correlation"]
    lines.append(f"| spearman_urgency_correlation | {f'{rho:.3f}' if rho is not None else 'n/a'} |")
    lines += ["", f"![confusion matrix]({cm_path.name})", ""]
    if misses:
        lines += ["## Root-cause misses", "", "| claim_id | actual | predicted |", "|---|---|---|"]
        lines += [f"| {cid} | {actual} | {predicted} |" for cid, actual, predicted in misses]
    else:
        lines += ["## Root-cause misses", "", "None — every scored claim matched ground truth."]
    return "\n".join(lines) + "\n"


def compare_versions(version_to_path: dict[str, Path], labels_path: Path, top_n: int = 10) -> str:
    labels = load_labels(labels_path)
    rows = []
    for version, path in version_to_path.items():
        results = load_run(path)
        pairs = _scoreable(results, labels)
        rows.append((
            version,
            root_cause_accuracy(pairs), routing_accuracy(pairs),
            critical_recall_at_n(results, labels, n=top_n),
            spearman_urgency_correlation(pairs),
        ))

    def fmt(v: Optional[float], pct: bool = True) -> str:
        if v is None:
            return "n/a"
        return f"{v:.1%}" if pct else f"{v:.3f}"

    lines = [
        "# Prompt-version comparison", "",
        f"Labels: `{labels_path}`", "",
        f"| version | root_cause_accuracy | routing_accuracy | critical_recall@{top_n} | spearman |",
        "|---|---|---|---|---|",
    ]
    for version, rc, route, recall, rho in rows:
        lines.append(f"| {version} | {fmt(rc)} | {fmt(route)} | {fmt(recall)} | {fmt(rho, pct=False)} |")
    report = "\n".join(lines) + "\n"
    (OUT_DIR / "prompt_version_comparison.md").write_text(report)
    return report


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--run")
    p.add_argument("--labels", required=True)
    p.add_argument("--compare", nargs="+", help='version=path pairs, e.g. v1=outputs/run_v1.json v2=...')
    p.add_argument("--top-n", type=int, default=10)
    args = p.parse_args()
    labels_path = Path(args.labels)
    labels_path = labels_path if labels_path.is_absolute() else ROOT / labels_path

    if args.compare:
        version_to_path = {}
        for pair in args.compare:
            version, _, raw_path = pair.partition("=")
            path = Path(raw_path)
            version_to_path[version] = path if path.is_absolute() else ROOT / path
        print(compare_versions(version_to_path, labels_path, args.top_n))
        return

    if not args.run:
        p.error("--run is required unless --compare is given")
    run_path = Path(args.run)
    run_path = run_path if run_path.is_absolute() else ROOT / run_path
    metrics = evaluate(run_path, labels_path, args.top_n)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
