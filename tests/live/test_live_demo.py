"""End-to-end demo scenarios on the real model.

    pytest tests/live -v                      # everything (≈30–60 min, depends on the GPU)
    pytest tests/live -v -k FrontPorch        # one scenario

Each scenario is skipped when its videos have not been imported (Import page) yet.
Outcomes per prompt and episode: ALERT, NEEDS_REVIEW or NONE, checked against the
accepted set in demo/scenarios.json. A markdown report is written to
<data dir>/reports/.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pytest

from app.analytics import condition_analyzer as ca
from app.analytics import engine, events_source, repo

SCENARIOS = json.loads((Path(__file__).resolve().parents[2] / "demo" / "scenarios.json").read_text(encoding="utf-8"))[
    "scenarios"]
REPORT: list[dict] = []


@pytest.fixture(scope="module", autouse=True)
def report(live_env):
    yield
    if not REPORT:
        return
    out = live_env["data"] / "reports" / f"live_report_{datetime.now():%Y%m%d_%H%M%S}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Live demo report", "", f"{datetime.now():%Y-%m-%d %H:%M} · thinking off · temporary analytics.db", "",
             "| Camera | Case | Prompt | Scope (expected) | Episode | Accepted | Got | Confidence | OK |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in REPORT:
        lines.append(f"| {r['camera']} | {r['case']} | {r['prompt']} | {r['scope']} ({r['expected_scope']}) | "
                     f"{r['episode']} | {' / '.join(r['accepted'])} | **{r['got']}** | {r['confidence']} | "
                     f"{'✅' if r['ok'] else '❌'} |")
    passed = sum(r["ok"] for r in REPORT)
    lines += ["", f"**{passed} / {len(REPORT)}** prompt-episode outcomes within the accepted set."]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nlive report: {out}")


@pytest.mark.parametrize("prompt,allowed", [
    ("Alert me if you see smoke.", {"frame"}),
    ("Alert me if someone falls to the ground.", {"event", "frame"}),
    ("Alert me if someone leaves a package at the door and then walks away.", {"history"}),
    ("Alert me if someone loiters in front of the entrance for a long time (longer than 30 seconds).", {"history"}),
])
def test_condition_analysis_scope(live_env, prompt, allowed):
    spec = ca.analyze(prompt, think=False)
    assert spec["scope"] in allowed, spec
    assert 1 <= len(spec["focus_questions"]) <= 6 and spec["decision_criteria"]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["camera_id"] for s in SCENARIOS])
def test_demo_scenario(live_env, scenario):
    cam = scenario["camera_id"]
    events = events_source.camera_events(cam)
    if not events:
        pytest.skip(f"camera {cam} has not been imported yet")
    imported = {s["filename"] for s in events_source.sources(cam)}
    missing = [v["file"] for v in scenario["videos"] if v["file"] not in imported]
    if missing:
        pytest.skip(f"{cam}: videos not imported yet: {missing}")

    started = time.time()
    conditions = []
    for c in scenario["conditions"]:
        spec = ca.validate(ca.analyze(c["prompt"], think=False))
        conditions.append((repo.create_condition(cam, spec), c))
    repo.enqueue_events(events, "QUEUED")
    for ev in events:  # in event order, like the worker
        engine.process_event(ev["event_id"])
    for ep in repo.open_episodes():
        if ep["camera_id"] == cam:
            engine.close_episode(ep, "end of the recording (test)")

    episodes = sorted(repo.camera_episodes(cam), key=lambda e: e["start_iso"])
    failures = []
    for cond, c in conditions:
        for i, ep in enumerate(episodes):
            accepted = c["accepted"][i] if i < len(c["accepted"]) else ["NONE"]
            alert = repo.current_alert(cond["id"], ep["id"])
            got = "NONE" if alert is None else ("ALERT" if alert["kind"] == "DETECTED" else "NEEDS_REVIEW")
            ok = got in accepted
            REPORT.append({"camera": cam, "case": scenario["case"], "prompt": c["prompt"], "scope": cond["scope"],
                           "expected_scope": c.get("expected_scope"), "episode": f"#{i + 1} ({ep['event_count']} ev)",
                           "accepted": accepted, "got": got,
                           "confidence": f"{alert['confidence']:.2f}" if alert and alert["confidence"] is not None else "–",
                           "ok": ok})
            if not ok:
                failures.append(f"\"{c['prompt']}\" episode {i + 1}: got {got}, accepted {accepted}")
    print(f"{cam}: {len(events)} events, {len(episodes)} episode(s), {time.time() - started:.0f} s")
    assert not failures, "; ".join(failures)
