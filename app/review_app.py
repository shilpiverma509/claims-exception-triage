"""Streamlit ranked-queue review UI (T11).

The assistant proposes; a human disposes here. Every Approve/Reject/Reassign
click is appended to the same audit.jsonl the pipeline writes to — nothing
in this app executes an action, it only records a human decision against it.

Run:
  streamlit run app/review_app.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from triage import audit, config  # noqa: E402

OUT_DIR = ROOT / "outputs"
AUDIT_PATH = ROOT / "outputs" / "audit.jsonl"

BAND_COLOR = {"CRITICAL": "#c0392b", "HIGH": "#e67e22", "MEDIUM": "#f1c40f", "LOW": "#2ecc71"}
MODE_LABEL = {"live": "🟢 LIVE", "cached": "🟡 CACHED", "mock": "🔵 MOCK", "baseline": "⚪ BASELINE"}

st.set_page_config(page_title="Claims Exception Triage — Review Queue", layout="wide")


@st.cache_data(show_spinner=False)
def load_run(path: str) -> list[dict]:
    return json.loads(Path(path).read_text())


def load_audit() -> list[dict]:
    if not AUDIT_PATH.exists():
        return []
    return [json.loads(line) for line in AUDIT_PATH.read_text().splitlines() if line.strip()]


def latest_decisions(rows: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in rows:
        if row["stage"] == "human_decision":
            latest[row["claim_id"]] = row
    return latest


def redaction_notices(rows: list[dict]) -> dict[str, list[dict]]:
    notices: dict[str, list[dict]] = {}
    for row in rows:
        if row["stage"] == "redact":
            notices.setdefault(row["claim_id"], []).append(row["outcome"])
    return notices


def record_decision(claim_id: str, decision: str, note: str = "") -> None:
    audit.log("human_decision", claim_id, {"decision": decision, "note": note},
               actor="analyst_streamlit")


def run_picker() -> Path | None:
    runs = sorted(OUT_DIR.glob("triage_*.json"))
    runs = [p for p in runs if not p.name.endswith("_errors.json")]
    if not runs:
        st.error(f"No triage runs found in {OUT_DIR}. Run `make triage` first.")
        return None
    labels = {p.name: p for p in runs}
    choice = st.sidebar.selectbox("Triage run", list(labels.keys()), index=len(labels) - 1)
    return labels[choice]


def mode_banner(results: list[dict]) -> None:
    mode = results[0]["mode"] if results else "unknown"
    label = MODE_LABEL.get(mode, mode.upper())
    st.sidebar.markdown(f"**Mode:** {label}")
    if mode == "live":
        st.sidebar.caption("Calling the real Anthropic API — costs money, needs ANTHROPIC_API_KEY.")
    elif mode == "cached":
        st.sidebar.caption("Replaying cached LLM responses — safe for offline demo.")
    elif mode == "mock":
        st.sidebar.caption("Deterministic heuristic, no network — dev/demo fallback only.")


def render_claim(row: dict, decisions: dict[str, dict], notices: dict[str, list[dict]]) -> None:
    claim = row["claim"]
    assessment = row["assessment"]
    cid = claim["claim_id"]
    band = row["final_band"] or config.band_for(row["final_urgency"])
    color = BAND_COLOR.get(band, "#7f8c8d")
    decision = decisions.get(cid)

    header = (f":{'red' if band == 'CRITICAL' else 'orange' if band == 'HIGH' else 'blue'}"
              f"[**{band}** {row['final_urgency']}] {cid} — {claim['provider_name']} "
              f"· ${claim['billed_amount_cents'] / 100:,.2f} · {row['final_queue']}")
    with st.expander(header, expanded=False):
        left, right = st.columns([2, 1])

        with left:
            st.markdown(f"**Summary:** {assessment['summary']}")
            st.markdown(f"**Root cause:** `{assessment['root_cause']}` "
                        f"· **confidence:** {assessment['confidence']:.2f}")
            st.markdown(f"**Proposed action:** `{assessment['proposed_action']}`")
            st.markdown("**Urgency reasons:**")
            for reason in assessment["urgency_reasons"]:
                st.markdown(f"- {reason}")

            if cid in notices:
                fields = sorted({f for n in notices[cid] for f in n.get("findings", [])})
                st.warning(f"PHI-like patterns redacted before this claim reached the LLM: "
                           f"{', '.join(fields)}")

            if row["guard"]["adjustments"]:
                st.info("Guard adjustments:\n" + "\n".join(f"- {a}" for a in row["guard"]["adjustments"]))

            st.markdown("**Claim detail**")
            st.json({k: v for k, v in claim.items() if k not in ("adjudicator_note",)}, expanded=False)
            st.markdown(f"**Adjudicator note:** {claim['adjudicator_note']}")

            evidence = row.get("evidence")
            if evidence:
                st.markdown("**Source-system evidence**")
                st.json(evidence, expanded=False)

        with right:
            if decision:
                st.success(f"{decision['outcome']['decision'].upper()} by {decision['actor']} "
                           f"at {decision['ts'][:19]}")
            st.button("✅ Approve", key=f"approve_{cid}",
                      on_click=record_decision, args=(cid, "approve"))
            st.button("❌ Reject", key=f"reject_{cid}",
                      on_click=record_decision, args=(cid, "reject"))
            reassign_to = st.selectbox("Reassign to", list(config.TEAMS.keys()),
                                       format_func=lambda q: config.TEAMS[q].name,
                                       key=f"reassign_select_{cid}")
            st.button("↪ Reassign", key=f"reassign_{cid}",
                      on_click=record_decision, args=(cid, "reassign", reassign_to.value))


def queue_tab(results: list[dict]) -> None:
    audit_rows = load_audit()
    decisions = latest_decisions(audit_rows)
    notices = redaction_notices(audit_rows)

    bands = st.multiselect("Filter by band", list(config.URGENCY_BANDS.keys()),
                           default=list(config.URGENCY_BANDS.keys()))
    filtered = [r for r in results if (r["final_band"] or config.band_for(r["final_urgency"])) in bands]
    st.caption(f"{len(filtered)} of {len(results)} claims shown, "
               f"{sum(1 for r in results if r['guard']['forced_human_review'])} flagged for human review")

    for row in sorted(filtered,
                      key=lambda r: (r["final_urgency"], r["claim"]["billed_amount_cents"]),
                      reverse=True):
        render_claim(row, decisions, notices)


def audit_tab() -> None:
    rows = load_audit()
    if not rows:
        st.info("No audit entries yet.")
        return
    claim_filter = st.text_input("Filter by claim_id (optional)")
    shown = [r for r in reversed(rows) if not claim_filter or r["claim_id"] == claim_filter]
    st.dataframe(
        [{"ts": r["ts"], "claim_id": r["claim_id"], "stage": r["stage"], "actor": r["actor"],
          "model": r["model"], "prompt_version": r["prompt_version"],
          "outcome": json.dumps(r["outcome"], default=str)[:200]} for r in shown],
        use_container_width=True, hide_index=True,
    )


def main() -> None:
    st.title("Claims Exception Triage — Review Queue")
    run_path = run_picker()
    if run_path is None:
        return
    results = load_run(str(run_path))
    mode_banner(results)

    tab_queue, tab_audit = st.tabs(["Ranked Queue", "Audit Trail"])
    with tab_queue:
        queue_tab(results)
    with tab_audit:
        audit_tab()


if __name__ == "__main__":
    main()
