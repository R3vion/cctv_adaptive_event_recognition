"""Create the demo prompts from demo/scenarios.json through the running application's API.

    python -m scripts.seed_demo                      # app on http://localhost:8000
    python -m scripts.seed_demo --url http://host:8000 --backfill
    docker compose exec app python -m scripts.seed_demo

Prompts that already exist for a camera (same text) are skipped. Each new prompt is
analysed by the LLM first (scope, focus questions, criteria), exactly as on the UI.
With --backfill the prompts are also run on events that were already analysed.
The videos themselves are imported by hand on the Import page.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

SCENARIOS = Path(__file__).resolve().parent.parent / "demo" / "scenarios.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8000", help="base URL of the running app")
    parser.add_argument("--backfill", action="store_true", help="also run new prompts on already analysed events")
    parser.add_argument("--camera", help="only this camera id")
    args = parser.parse_args()

    scenarios = json.loads(SCENARIOS.read_text(encoding="utf-8"))["scenarios"]
    with httpx.Client(base_url=args.url, timeout=600) as client:
        try:
            existing = client.get("/api/cameras").json()["cameras"]
        except httpx.HTTPError as exc:
            print(f"Cannot reach the app at {args.url}: {exc}", file=sys.stderr)
            return 1
        known = {c["camera_id"] for c in existing}
        for sc in scenarios:
            cam = sc["camera_id"]
            if args.camera and cam != args.camera:
                continue
            have = set()
            if cam in known:
                have = {c["prompt"] for c in client.get(f"/api/cameras/{cam}").json()["conditions"]}
            for cond in sc["conditions"]:
                if cond["prompt"] in have:
                    print(f"= {cam}: already has \"{cond['prompt']}\"")
                    continue
                print(f"+ {cam}: analysing \"{cond['prompt']}\" …", flush=True)
                resp = client.post("/api/conditions/analyze", json={"prompt": cond["prompt"]})
                resp.raise_for_status()
                spec = resp.json()
                body = {k: spec[k] for k in ("prompt", "title", "scope", "decision_criteria", "suggestion", "llm_call_id")}
                body["focus_questions"] = [q["question"] for q in spec["focus_questions"]]
                body["backfill"] = args.backfill
                created = client.post(f"/api/cameras/{cam}/conditions", json=body)
                created.raise_for_status()
                c = created.json()
                note = "" if c["scope"] == cond.get("expected_scope") else f" (scenario expected {cond.get('expected_scope')})"
                print(f"  → prompt #{c['id']}: {c['scope']}{note} – {c['title']}")
    print("Done. Import the videos on the Import page (see demo/scenarios.json for camera ids).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
