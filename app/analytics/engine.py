"""The analysis pipeline for one camera event.

event → episode → frames (adaptive selection) → shared, focused observation (VLM, cached)
      → per prompt decision (text LLM; frame / event / history scope) → alerts

All functions are synchronous; the worker runs them in a thread.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

from app.analytics import alerts, events_source, repo
from app.analytics.bus import bus, plog
from app.config import get_settings
from app.llm import prompts
from app.llm.ollama_client import ImageIn, LLMError, client
from app.llm.schemas import (ClosingDecisionOut, EpisodeSummaryOut, HistoryDecisionOut, ObservationOut,
                             SingleDecisionOut)
from app.media import mjpeg
from app.selection.selector import frame_scope_subset
from app.selection.service import select_for_clip


class ObservationFailed(RuntimeError):
    pass


# --- small helpers ------------------------------------------------------------------
def _iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def with_end(event: dict) -> dict:
    end = _iso(event["timestamp_iso"]) + timedelta(seconds=float(event["duration_s"]))
    return {**event, "end_iso": end.isoformat(timespec="seconds")}


def _hash(value) -> str:
    return hashlib.sha1(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def _qid(condition: dict, qid: str) -> str:
    return f"V{condition['current_version_id']}.{qid}"


def _question_lines(condition: dict) -> str:
    return "\n".join(f"- {q['id']}: {q['question']}" for q in condition["focus_questions"])


def is_stopped(condition_id: int, episode_id: int) -> bool:
    """The prompt already said YES in this episode → it stops for the rest of the episode."""
    return any(e["decision"] == "YES" and not e["is_closing"]
               for e in repo.current_evaluations(condition_id, episode_id))


def episode_events(episode_id: int) -> list[dict]:
    ids = repo.episode_event_ids(episode_id)
    found = events_source.events_by_ids(ids)
    return [found[i] for i in ids if i in found]


# --- episodes --------------------------------------------------------------------------
def assign_episode(event: dict) -> dict:
    """Join the camera's latest episode if the gap allows it, otherwise close it and open a new one."""
    ev = with_end(event)
    latest = repo.latest_episode(event["camera_id"])
    gap_limit = float(get_settings().episode_gap_s)
    if latest:
        gap = (_iso(ev["timestamp_iso"]) - _iso(latest["last_event_iso"])).total_seconds()
        if 0 <= gap <= gap_limit or ev["timestamp_iso"] <= latest["last_event_iso"]:
            if latest["status"] == "CLOSED":
                for cid in repo.conditions_in_episode(latest["id"]):
                    repo.supersede_evaluations(cid, latest["id"], closing_only=True)
                plog("episode", f"episode #{latest['id']} reopened (gap {gap:.0f} s)", camera_id=event["camera_id"],
                     event_id=event["event_id"])
            episode = repo.extend_episode(latest["id"], ev)
            repo.set_queue_status(event["event_id"], "PROCESSING", episode_id=episode["id"])
            return episode
        if latest["status"] == "OPEN":
            close_episode(latest, f"gap of {gap:.0f} s before {event['event_id']}")
    episode = repo.create_episode(ev)
    repo.set_queue_status(event["event_id"], "PROCESSING", episode_id=episode["id"])
    plog("episode", f"new episode #{episode['id']} starts", camera_id=event["camera_id"], event_id=event["event_id"])
    bus.publish("episode", episode_id=episode["id"], status="OPEN", camera_id=event["camera_id"])
    return episode


# --- frames & observation ------------------------------------------------------------------
def frame_set(event: dict, conditions: list[dict]) -> list[dict]:
    """Selected frames of the event; frame-scope-only cameras get the small subset."""
    sel = repo.get_selection(event["event_id"])
    if sel is None:
        sel = select_for_clip(events_source.abs_path(event["clip_path"]), event["fps"], event["trigger_offset_s"])
        repo.save_selection(event["event_id"], event["camera_id"], event["event_no"], sel)
        plog("frames", f"no stored selection – computed one from the clip ({len(sel['frames'])} frames)",
             camera_id=event["camera_id"], event_id=event["event_id"])
    frames = sel["frames"]
    if conditions and all(c["scope"] == "frame" for c in conditions):
        frames = frame_scope_subset(frames, int(get_settings().frame_scope_frames))
    return [{"n": i + 1, "t": f["t"], "kinds": f["kinds"], "score": f["score"]} for i, f in enumerate(frames)]


def observe(event: dict, frames: list[dict], conditions: list[dict], *, force: bool = False) -> dict:
    version_ids = [c["current_version_id"] for c in conditions]
    frames_hash = _hash({"clip": event["clip_path"], "t": [round(f["t"], 3) for f in frames]})
    if not force:
        cached = repo.find_observation(event["event_id"], frames_hash, version_ids)
        if cached:
            plog("observe", f"observation cache hit (#{cached['id']})", camera_id=event["camera_id"],
                 event_id=event["event_id"])
            return cached

    clip = events_source.abs_path(event["clip_path"])
    count = mjpeg.frame_count(clip)
    jpegs = mjpeg.read_frames(clip, [mjpeg.index_at(f["t"], event["fps"], count) for f in frames])
    labels = [f"Frame {f['n']}: t={f['t']:.2f}s ({', '.join(f['kinds'])})" for f in frames]
    questions = [f"- {_qid(c, q['id'])}: {q['question']}" for c in conditions for q in c["focus_questions"]]
    system, user = prompts.render("observation", camera_id=event["camera_id"], event_id=event["event_id"],
                                  timestamp=event["timestamp"], duration=f"{event['duration_s']:.1f}",
                                  frames="\n".join(labels), questions="\n".join(questions))
    plog("observe", f"VLM observation of {len(frames)} frames for {len(conditions)} prompt(s)…",
         camera_id=event["camera_id"], event_id=event["event_id"])
    try:
        out, call_id = client.chat("observation", system, user, ObservationOut,
                                   think=bool(get_settings().think_observation),
                                   images=[ImageIn(lbl, j) for lbl, j in zip(labels, jpegs)])
    except LLMError as exc:
        raise ObservationFailed(str(exc)) from exc
    best = out.best_frame if 1 <= out.best_frame <= len(frames) else 1
    rec = {
        "event_id": event["event_id"], "camera_id": event["camera_id"], "event_no": event["event_no"],
        "frames_hash": frames_hash, "focus_hash": _hash(questions), "covered_versions": version_ids,
        "frames": frames, "description": out.description.strip(),
        "focus_answers": [a.model_dump() for a in out.focus_answers], "best_frame": best, "llm_call_id": call_id,
    }
    rec["id"] = repo.insert_observation(rec)
    plog("observe", out.description.strip()[:220], camera_id=event["camera_id"], event_id=event["event_id"],
         data={"observation_id": rec["id"]})
    return rec


def observation_text(obs: dict, condition: dict, event: dict | None = None, episode_start: str | None = None) -> str:
    """The observation as the decision model sees it (only this prompt's answers, ids without prefix)."""
    prefix = f"V{condition['current_version_id']}."
    answers = []
    for a in obs["focus_answers"]:
        if a["id"].startswith(prefix):
            frame = f", frame {a['frame']}" if a.get("frame", -1) > 0 else ""
            answers.append(f"  {a['id'][len(prefix):]}: {a['answer']} – {a['evidence']}{frame}")
    head = ""
    if event is not None:
        offset = ""
        if episode_start:
            offset = f", +{(_iso(event['timestamp_iso']) - _iso(episode_start)).total_seconds():.0f} s from episode start"
        head = f"Event #{event['event_no']:03d} at {event['timestamp']}{offset} (clip {event['duration_s']:.1f} s, " \
               f"{len(obs['frames'])} frames)\n"
    return (f"{head}Description: {obs['description']}\n"
            f"Focus answers:\n{chr(10).join(answers) if answers else '  (this prompt was not asked in this event)'}\n"
            f"Best frame: {obs.get('best_frame')}")


# --- decisions ---------------------------------------------------------------------------
def _focus_results(condition: dict, results, event_no: int | None) -> list[dict]:
    by_id = {r.id.split(".")[-1]: r for r in results}
    out = []
    for q in condition["focus_questions"]:
        r = by_id.get(q["id"])
        out.append({"id": q["id"], "question": q["question"], "status": r.status if r else "unknown",
                    "evidence": r.evidence if r else "not reported", "event_no": event_no})
    return out


def camera_memory(camera_id: str, before_iso: str) -> str:
    hours = float(get_settings().camera_memory_hours)
    if hours <= 0:
        return "(camera memory disabled)"
    since = (_iso(before_iso) - timedelta(hours=hours)).isoformat(timespec="seconds")
    lines = []
    for ep in repo.memory_episodes(camera_id, before_iso, since):
        lines.append(f"- {ep['start_iso'].replace('T', ' ')} – {ep['end_iso'][11:]} ({ep['event_count']} events): "
                     f"{ep.get('summary') or '(no summary)'}")
    return "\n".join(lines) if lines else "(no earlier episodes in the memory window)"


def _episode_observations(condition: dict, episode: dict, upto: dict | None, current_obs: dict | None) -> str:
    blocks = []
    evals = {e["event_id"]: e for e in repo.current_evaluations(condition["id"], episode["id"]) if not e["is_closing"]}
    for ev in episode_events(episode["id"]):
        if upto is not None and ev["timestamp_iso"] > upto["timestamp_iso"]:
            break
        obs = None
        if upto is not None and ev["event_id"] == upto["event_id"]:
            obs = current_obs
        elif evals.get(ev["event_id"], {}).get("observation_id"):
            obs = repo.get_observation(evals[ev["event_id"]]["observation_id"])
        if obs is None:
            found = repo.observations_for_event(ev["event_id"])
            obs = found[0] if found else None
        if obs is None:
            blocks.append(f"Event #{ev['event_no']:03d} at {ev['timestamp']}: (not observed)")
        else:
            blocks.append(observation_text(obs, condition, ev, episode["start_iso"]))
    return "\n\n".join(blocks) if blocks else "(none)"


def decide(condition: dict, run_id: int, event: dict, episode: dict, obs: dict) -> dict:
    settings = get_settings()
    base = {"run_id": run_id, "condition_id": condition["id"], "condition_version_id": condition["current_version_id"],
            "camera_id": event["camera_id"], "episode_id": episode["id"], "event_id": event["event_id"],
            "event_no": event["event_no"], "observation_id": obs["id"]}
    common = dict(prompt=condition["prompt"], criteria=condition["decision_criteria"],
                  questions=_question_lines(condition))
    try:
        if condition["scope"] == "history":
            system, user = prompts.render(
                "decision_history", **common, camera_id=event["camera_id"],
                memory=camera_memory(event["camera_id"], episode["start_iso"]),
                observations=_episode_observations(condition, episode, event, obs))
            out, call_id = client.chat("decision", system, user, HistoryDecisionOut, think=bool(settings.think_decision))
            key_event_no = out.key_event_no if out.key_event_no else event["event_no"]
        else:
            system, user = prompts.render("decision_single", **common, event_id=event["event_id"],
                                          timestamp=event["timestamp"], observation=observation_text(obs, condition))
            out, call_id = client.chat("decision", system, user, SingleDecisionOut, think=bool(settings.think_decision))
            key_event_no = event["event_no"]
        rec = {**base, "decision": out.decision, "confidence": round(float(out.confidence), 3),
               "reasoning": out.reasoning.strip(), "focus_results": _focus_results(condition, out.focus_results,
                                                                                   key_event_no),
               "key_event_no": key_event_no, "key_frame": out.key_frame, "llm_call_id": call_id}
    except LLMError as exc:
        rec = {**base, "decision": "ERROR", "reasoning": str(exc)[:500], "llm_call_id": exc.call_id}
    repo.supersede_evaluations(condition["id"], episode["id"], event_id=event["event_id"])
    rec["id"] = repo.insert_evaluation(rec)
    level = "error" if rec["decision"] == "ERROR" else "info"
    plog("decide", f"\"{condition['title']}\" ({condition['scope']}): {rec['decision']}"
         f"{' %.2f' % rec['confidence'] if rec.get('confidence') is not None else ''} – {rec.get('reasoning', '')[:180]}",
         level=level, camera_id=event["camera_id"], event_id=event["event_id"],
         data={"condition_id": condition["id"], "evaluation_id": rec["id"]})
    bus.publish("evaluation", event_id=event["event_id"], camera_id=event["camera_id"],
                condition_id=condition["id"], decision=rec["decision"])
    alerts.reconcile(condition["id"], episode["id"])
    return rec


def _skip(condition: dict, run_id: int, event: dict, episode: dict) -> None:
    repo.supersede_evaluations(condition["id"], episode["id"], event_id=event["event_id"])
    repo.insert_evaluation({"run_id": run_id, "condition_id": condition["id"],
                            "condition_version_id": condition["current_version_id"], "camera_id": event["camera_id"],
                            "episode_id": episode["id"], "event_id": event["event_id"], "event_no": event["event_no"],
                            "decision": "SKIPPED", "reasoning": "Already alerted in this episode – the prompt stops "
                                                                "after its first YES."})


def evaluate_event(event: dict, episode: dict, targets: list[tuple[dict, int]], *, force_observe: bool = False,
                   respect_stop: bool = True) -> None:
    """Observe the event once for every target prompt and decide per prompt."""
    todo = []
    for condition, run_id in targets:
        if respect_stop and is_stopped(condition["id"], episode["id"]):
            _skip(condition, run_id, event, episode)
        else:
            todo.append((condition, run_id))
    if not todo:
        plog("decide", "all prompts already alerted in this episode – skipped", camera_id=event["camera_id"],
             event_id=event["event_id"])
        return
    conditions = [c for c, _ in todo]
    frames = frame_set(event, conditions)
    try:
        obs = observe(event, frames, conditions, force=force_observe)
    except ObservationFailed as exc:
        for condition, run_id in todo:
            repo.supersede_evaluations(condition["id"], episode["id"], event_id=event["event_id"])
            repo.insert_evaluation({"run_id": run_id, "condition_id": condition["id"],
                                    "condition_version_id": condition["current_version_id"],
                                    "camera_id": event["camera_id"], "episode_id": episode["id"],
                                    "event_id": event["event_id"], "event_no": event["event_no"],
                                    "decision": "ERROR", "reasoning": f"Observation failed: {exc}"[:500]})
        raise
    for condition, run_id in todo:
        decide(condition, run_id, event, episode, obs)


# --- live processing -----------------------------------------------------------------------
def process_event(event_id: str) -> None:
    event = events_source.get_event(event_id)
    if event is None:
        repo.set_queue_status(event_id, "ERROR", error="event not found in events.db")
        return
    bus.publish("queue", event_id=event_id, camera_id=event["camera_id"], status="PROCESSING")
    episode = assign_episode(event)
    conditions = repo.list_conditions(event["camera_id"], active_only=True)
    try:
        if not conditions:
            plog("decide", "no active prompts for this camera", camera_id=event["camera_id"], event_id=event_id)
        else:
            targets = [(c, repo.live_run(c, episode["id"])) for c in conditions]
            evaluate_event(event, episode, targets)
    except ObservationFailed as exc:
        repo.set_queue_status(event_id, "ERROR", error=str(exc)[:500], episode_id=episode["id"])
        repo.touch_episode(episode["id"])
        plog("observe", f"observation failed: {exc}", level="error", camera_id=event["camera_id"], event_id=event_id)
        bus.publish("queue", event_id=event_id, camera_id=event["camera_id"], status="ERROR")
        return
    repo.touch_episode(episode["id"])
    repo.set_queue_status(event_id, "DONE", episode_id=episode["id"])
    bus.publish("queue", event_id=event_id, camera_id=event["camera_id"], status="DONE")


# --- episode close -----------------------------------------------------------------------------
def closing_decision(condition: dict, run_id: int, episode: dict) -> dict | None:
    events = episode_events(episode["id"])
    if not events:
        return None
    last = events[-1]
    system, user = prompts.render(
        "decision_closing", prompt=condition["prompt"], criteria=condition["decision_criteria"],
        questions=_question_lines(condition), camera_id=episode["camera_id"],
        memory=camera_memory(episode["camera_id"], episode["start_iso"]),
        observations=_episode_observations(condition, episode, None, None))
    base = {"run_id": run_id, "condition_id": condition["id"], "condition_version_id": condition["current_version_id"],
            "camera_id": episode["camera_id"], "episode_id": episode["id"], "event_id": last["event_id"],
            "event_no": last["event_no"], "is_closing": True}
    try:
        out, call_id = client.chat("closing", system, user, ClosingDecisionOut,
                                   think=bool(get_settings().think_decision))
        rec = {**base, "decision": out.decision, "confidence": round(float(out.confidence), 3),
               "reasoning": out.reasoning.strip(),
               "focus_results": _focus_results(condition, out.focus_results, out.key_event_no),
               "key_event_no": out.key_event_no or last["event_no"], "key_frame": out.key_frame, "llm_call_id": call_id}
    except LLMError as exc:
        rec = {**base, "decision": "ERROR", "reasoning": str(exc)[:500], "llm_call_id": exc.call_id}
    repo.supersede_evaluations(condition["id"], episode["id"], closing_only=True)
    rec["id"] = repo.insert_evaluation(rec)
    plog("close", f"closing decision \"{condition['title']}\": {rec['decision']} – {rec.get('reasoning', '')[:180]}",
         level="error" if rec["decision"] == "ERROR" else "info", camera_id=episode["camera_id"],
         data={"condition_id": condition["id"], "evaluation_id": rec["id"]})
    return rec


def summarize_episode(episode: dict) -> None:
    events = episode_events(episode["id"])
    blocks = []
    for ev in events:
        found = repo.observations_for_event(ev["event_id"])
        if found:
            blocks.append(f"Event #{ev['event_no']:03d} at {ev['timestamp']}: {found[0]['description']}")
    if not blocks:
        return
    system, user = prompts.render("episode_summary", camera_id=episode["camera_id"],
                                  start=episode["start_iso"].replace("T", " "), end=episode["end_iso"].replace("T", " "),
                                  count=len(events), observations="\n".join(blocks))
    try:
        out, call_id = client.chat("summary", system, user, EpisodeSummaryOut, think=False)
    except LLMError as exc:
        plog("close", f"episode summary failed: {exc}", level="error", camera_id=episode["camera_id"])
        return
    repo.set_episode_summary(episode["id"], out.summary.strip(), call_id)
    plog("close", f"episode #{episode['id']} summary: {out.summary.strip()[:220]}", camera_id=episode["camera_id"])


def close_episode(episode: dict, reason: str, runs: dict[int, int] | None = None) -> None:
    """Close: closing decisions for history prompts, summary for the camera memory, reconcile alerts."""
    repo.close_episode(episode["id"], reason)
    episode = repo.get_episode(episode["id"])
    plog("close", f"episode #{episode['id']} closed ({reason})", camera_id=episode["camera_id"])
    involved = set(repo.conditions_in_episode(episode["id"]))
    for cid in involved:
        condition = repo.get_condition(cid)
        if (condition is None or condition["deleted"] or condition["scope"] != "history"
                or is_stopped(cid, episode["id"])):
            continue
        evals = [e for e in repo.current_evaluations(cid, episode["id"]) if not e["is_closing"]]
        if not any(e["decision"] not in ("SKIPPED", "ERROR") for e in evals):
            continue
        run_id = (runs or {}).get(cid) or repo.live_run(condition, episode["id"])
        closing_decision(condition, run_id, episode)
    summarize_episode(episode)
    for cid in involved:
        alerts.reconcile(cid, episode["id"])
    repo.finish_live_runs(episode["id"])
    bus.publish("episode", episode_id=episode["id"], status="CLOSED", camera_id=episode["camera_id"])


def close_idle_episodes() -> bool:
    idle = float(get_settings().episode_idle_close_s)
    closed = False
    for ep in repo.open_episodes():
        quiet = (datetime.now() - _iso(ep["last_activity_at"])).total_seconds()
        if quiet >= idle and repo.pending_for_camera(ep["camera_id"]) == 0:
            close_episode(ep, f"idle for {quiet:.0f} s")
            closed = True
    return closed


# --- re-runs ---------------------------------------------------------------------------------
def replay_episode(condition: dict, episode: dict, kind: str, *, force_observe: bool = False) -> None:
    """Re-evaluate a prompt over a whole episode in time order (each event only sees earlier ones)."""
    repo.supersede_evaluations(condition["id"], episode["id"])
    run_id = repo.create_run(kind, condition, episode["id"])
    plog("rerun", f"replaying episode #{episode['id']} for \"{condition['title']}\" (run #{run_id})",
         camera_id=episode["camera_id"])
    try:
        for event in episode_events(episode["id"]):
            evaluate_event(event, episode, [(condition, run_id)], force_observe=force_observe)
        episode = repo.get_episode(episode["id"])
        if episode["status"] == "CLOSED" and condition["scope"] == "history" and not is_stopped(condition["id"],
                                                                                               episode["id"]):
            closing_decision(condition, run_id, episode)
        alerts.reconcile(condition["id"], episode["id"])
        repo.finish_run(run_id)
    except ObservationFailed as exc:
        repo.finish_run(run_id, "ERROR", str(exc)[:500])
        alerts.reconcile(condition["id"], episode["id"])
        raise


def rerun_event(event_id: str, condition_id: int, *, force_observe: bool = False) -> None:
    event = events_source.get_event(event_id)
    condition = repo.get_condition(condition_id)
    queue = repo.queue_for_camera(event["camera_id"]).get(event_id) if event else None
    if not event or not condition or not queue or not queue.get("episode_id"):
        raise ValueError("event, prompt or episode not found (was the event analysed already?)")
    episode = repo.get_episode(queue["episode_id"])
    if condition["scope"] == "history":
        replay_episode(condition, episode, "rerun_episode", force_observe=force_observe)
        return
    run_id = repo.create_run("rerun_event", condition, episode["id"])
    plog("rerun", f"re-running \"{condition['title']}\" on {event_id} (run #{run_id})", camera_id=event["camera_id"],
         event_id=event_id)
    try:
        evaluate_event(event, episode, [(condition, run_id)], force_observe=force_observe, respect_stop=False)
        repo.finish_run(run_id)
    except ObservationFailed as exc:
        repo.finish_run(run_id, "ERROR", str(exc)[:500])
        raise


def backfill(condition_id: int, *, force_observe: bool = False) -> None:
    """Run a prompt on every already analysed episode of its camera."""
    condition = repo.get_condition(condition_id)
    if condition is None:
        raise ValueError("prompt not found")
    episodes = sorted(repo.camera_episodes(condition["camera_id"]), key=lambda e: e["start_iso"])
    if not episodes:
        plog("rerun", f"no analysed episodes yet for \"{condition['title']}\"", camera_id=condition["camera_id"])
    for ep in episodes:
        replay_episode(condition, ep, "backfill", force_observe=force_observe)
