# Specification – CCTV adaptive event recognition

This is the full functional specification of the prototype. It extends the original
assignment with every decision made during the design sessions. For the *why*, see
[DESIGN_NOTES.md](DESIGN_NOTES.md); for a quick tour, see the [README](../README.md).

---

## 1. Product context

An analytics component for a CCTV camera backend. For each connected camera the user
describes in free natural language **when they want to be alerted**, e.g.

- "Alert me if you see smoke."
- "Alert me if someone is running away."
- "Alert me if someone loiters in front of the entrance for a long time."
- "Alert me if someone leaves a package at the door and then walks away."

There is no predefined event list – the prompt defines the event. A camera can have
**several prompts**, each can be enabled/disabled independently.

## 2. The camera system and its simulation

The (assumed) camera system has a basic detector (motion / person / vehicle). When it
fires it sends a camera event; if the situation still holds 10 s later, a new event
arrives – so one real-world happening may appear as several consecutive events.

There is no real camera here. The camera system is **simulated by a web import page**
that writes the camera system's database, `events.db`.

### 2.1 Camera event fields

| Field | Rule |
|---|---|
| Event id | `<camera_id>#<NNN>`, e.g. `FrontPorch#003`. `NNN` is a per-camera running number (zero padded to at least 3 digits). A new video for an existing camera continues the numbering. |
| Camera id | Defaults to the uploaded file name without extension; editable on import, or an existing camera can be picked from a list. Allowed characters: `A-Z a-z 0-9 _ -`. |
| Timestamp | Displayed as `YYYY_MM_DD-hh_mm_ss`, stored additionally as sortable ISO. See 2.2. |
| Trigger frame | Default: middle of the segment; adjustable with a slider; stored as `trigger_frame.jpg`. |
| Clip | The 10 s segment as MJPEG (`chunk_NNN.mjpeg`). |
| Paths | Clip and trigger-frame paths are stored (relative to the data directory). |

### 2.2 Timestamps

- New camera: default start = moment of upload (browser local time).
- A **Today / Random** toggle (default Today). Random = random date within the last
  365 days with a random time of day.
- Existing camera: default start = last event + 10 s, freely editable. The page shows
  whether, with the current *episode gap* setting, the new video **joins the last
  episode or opens a new one**. The start must be later than the camera's last event.
- Each further segment is shifted by +10 s.

### 2.3 Splitting and storage

- Input: MP4. Videos longer than 10 s are cut into 10 s segments; the **remainder is
  always kept** as its own (shorter) segment. Shorter videos become one segment.
- Folder layout (all under `data/`):

```
data/
├─ events.db                      camera system database
├─ analytics.db                   analytics database
├─ source/<original file name>.mp4
├─ processed/<camera_id>/chunk_NNN/
│    ├─ chunk_NNN.mjpeg
│    └─ trigger_frame.jpg
└─ analytics/
     ├─ previews/<camera_id>/chunk_NNN.mp4   on-demand MP4 previews
     └─ alerts/<alert_id>/key_frame.jpg
```

- `chunk_NNN` numbering equals the event number (continues per camera).
- Uploading a **source file name** that was already imported is rejected (the camera id
  may repeat).

## 3. Import page

1. Drag & drop or browse an MP4.
2. Camera id field (default: file name) + list of existing cameras; start timestamp
   field; Today / Random toggle.
3. One **segment card** per segment:
   - metadata (event id, timestamp, duration, trigger time);
   - trigger-frame preview + its own slider (0 – segment length, default middle). The
     preview updates in the browser by seeking the source MP4; on save the server cuts
     that exact frame;
   - the **change chart** with the draggable threshold line, and a **filmstrip** of
     the selected frames below it (see 4);
   - a **Show preview** button under the trigger frame: loops the selected frames so that
     one loop always takes 3 s (30 frames → 10 fps, 10 frames → ~3.3 fps); it follows the
     threshold live.
4. **Save** writes the metadata to `events.db`, the raw MP4, clips and trigger frames to
   disk, and the per-segment frame selection to `analytics.db`. The import is final
   afterwards.

## 4. Adaptive frame selection

Goal: do not miss fast happenings (a package thrown in ~0.5 s) and still see slow ones
(smoke creeping down over a minute).

### 4.1 Change signals

Frames are sampled on a grid of `signal_fps` (default 15, capped at the video fps),
downscaled to `signal_side` px (long side, default 320), blurred, grey.

- **Changed-pixel ratio (%)**: share of pixels whose grey value differs by more than
  `pixel_delta`.
- **Histogram distance (%)**: Bhattacharyya distance of HSV colour histograms – catches
  scene-level change (smoke, fire, darkening).
- Score = `w_pixel · pixel + w_hist · histogram` (weights in Settings).

Two curves per segment:

- **Frame-to-frame change** (`motion`) – frame vs. previous frame. In `optical_flow`
  mode the pixel term is replaced by the share of pixels moving > 1 px (Farneback).
- **Change vs. last keyframe** (`ref`) – frame vs. the last selected keyframe. This
  curve accumulates slow change until it crosses the threshold.

### 4.2 Hybrid selection

1. **Mandatory frames**: first, trigger, last.
2. **Candidates**:
   - *keyframes*: walking the segment, a frame whose `ref` score reaches the threshold
     becomes a keyframe and the new reference;
   - *peaks*: local maxima of `motion` at or above the threshold (short, sharp events).
3. More candidates than `frames_max` → keep the best scores with **temporal NMS**
   (no near-duplicates around one peak).
4. Fewer frames than `frames_min` → **fill along cumulative change**: repeatedly insert a
   frame in the interval holding the most accumulated motion (a static segment degrades
   to uniform sampling).
5. **Cross-segment reference**: within one import, the reference chain continues from the
   previous segment's last keyframe, so slow change is compared with what was already
   seen. Mandatory frames do not reset the reference.

Limits: `frames_min = 5`, `frames_max = 30` per segment (0.5–3 fps on 10 s). When the user
**drags the threshold manually**, the limits no longer apply (the three mandatory frames
always stay).

**Default threshold – auto.** A single fixed value does not fit all videos (measured on the
test set: a night delivery peaks at ~2 % change, a busy shop at ~60 %). With
`selection_threshold = 0` (default) the threshold is calibrated per video from its own
frame-to-frame change: `median + k · 1.4826 · MAD` (robust noise level plus a margin,
`selection_auto_k = 6`, floor 0.5 %). Any other value is used as a fixed threshold.

### 4.3 Chart (one per segment)

- X: segment time (s); Y: change (%).
- Curves: frame-to-frame change and change vs. last keyframe.
- **Dashed threshold line**, draggable with the mouse. While dragging, the browser sends
  the new threshold (debounced ~100 ms) to the server, which recomputes with the same
  Python code and returns the selection (and the following segments, because the
  reference chain continues).
- Vertical markers for selected frames; distinct markers for trigger, first and last.
- Hover shows the frame thumbnail; a filmstrip of the selected frames sits below; a label
  shows e.g. "34 candidates → 30 selected".

The threshold can be changed **only during import**. Later the event page shows the chart
and filmstrip read-only.

Events that arrive without a stored selection (e.g. written by another system) get one
computed automatically from the clip with the default settings.

### 4.4 Use in analysis

- If every active prompt of the camera is *frame* scope: trigger frame + the best scoring
  frames, `frame_scope_frames` in total (default 3).
- Otherwise: the full selected set.
- Frames are sent in **original resolution** by default; `vlm_max_image_side` limits the
  long side. Each image is labelled with its number and time.
- **Auto-fit**: when the images of a call would not fit (~1 token per 1000 pixels), all of
  them are scaled down by the same factor so they take at most ~75 % of `num_ctx`.

## 5. Analytics

The analytics application **only reads** `events.db` (read-only SQLite connection) and
writes everything into `analytics.db`. Alerts reference the original events by event id
(`camera_id`, `event_no`).

### 5.1 Condition analysis (once, when a prompt is saved)

An LLM call (thinking on) turns the prompt into a **ConditionSpec**:

- `scope`: `frame` (decidable from one image) | `event` (motion / short temporal context
  within one 10 s event) | `history` (several events, temporal course);
- `focus_questions`: short, answerable visual questions for the VLM;
- `decision_criteria`: the decision rule in plain words.

The UI shows the proposal and **every field can be edited before saving**
(`scope_overridden` is recorded). Editing a prompt creates a **new immutable version**;
old evaluations keep pointing to the version they ran with.

### 5.2 Episodes

Two events of the same camera belong to one **episode** if their timestamps are at most
`episode_gap_s` apart (default 15 s). An episode is **closed** when

- a new event of that camera arrives with a larger gap, or
- no new event arrived for `episode_idle_close_s` (wall clock, default 30 s), or
- during a replay, after the last known event.

On close an LLM call writes a short **episode summary** (cached).

### 5.3 Observation (one VLM call per event, shared and focused)

One call per event (thinking off) with the selected frames. Structured JSON output:

- a general description (people, motion, objects, scene);
- answers to the focus questions of **all active prompts of the camera**, except prompts
  that already alerted in this episode; each answer cites a frame;
- the frame that shows the relevant evidence best.

Observations are cached: an observation is reused when it was made from the same frame set
and already answers the questions of the condition version in question. The full prompt
and the raw response are stored.

### 5.4 Decision (per prompt, text-only LLM call, thinking on)

- **frame / event scope**: from the current event's observation only →
  `YES | NO | UNCERTAIN`.
- **history scope**: from **all observations of the current episode so far** (no length
  limit) plus the **camera memory**: summaries of earlier closed episodes of the same
  camera within `camera_memory_hours` (default 24 h) → `YES | PENDING | NO`.

Every decision returns: decision, confidence (0–1), reasoning, a result per focus
question (✔ met / ✘ not met / ? unknown, with evidence and event reference) and the
key frame.

### 5.5 State machine (per prompt × episode)

- **First `YES`** → alert, and the prompt **stops** for the rest of the episode (later
  events get `SKIPPED`, its focus questions are left out of the shared observation). This
  holds for all three scopes: at most one alert per prompt per episode.
- **Episode close without `YES`**:
  - frame / event scope: any `UNCERTAIN` → **NEEDS_REVIEW** alert, otherwise no alert;
  - history scope: a **closing decision** call over the whole episode (+ camera memory):
    `YES` → alert; `UNCERTAIN` → **NEEDS_REVIEW**; `NO` → no alert.

Alerts are *derived* from the current evaluations, so re-running never duplicates them.

### 5.6 Runs and re-runs

Every evaluation belongs to a **run** (`live`, `rerun_event`, `rerun_episode`,
`backfill`). Each event has a **Re-run** button per prompt:

- frame / event scope: only that event is re-evaluated;
- history scope: the event's **episode is replayed causally from its start** in a new run
  – each event only sees earlier ones, exactly like live.

Cached observations are reused unless **force re-observe** is ticked. Older results stay
visible as history; replaced evaluations are marked superseded and replaced alerts become
**SUPERSEDED**. A prompt created after events already exist can be run on them with
**Run on existing events** (backfill).

### 5.7 Starting analysis

`analysis_mode`: **auto** (the background worker analyses new events in time order per
camera) or **manual** (an *Analyze* button per camera). Re-run is always available.
Per-event processing state: `WAITING`, `QUEUED`, `PROCESSING`, `DONE`, `ERROR`.

### 5.8 Alert content

- what triggered it: prompt text + version, camera, episode;
- **focus-question results** (✔ / ✘ / ?) with evidence;
- **confidence** and **reasoning**;
- **episode summary**: first/last timestamp, duration, short text;
- **key frame**, chosen by the model (no bounding box), saved as
  `data/analytics/alerts/<id>/key_frame.jpg` and shown prominently;
- the list of involved events.

Statuses: `ACTIVE`, `NEEDS_REVIEW`, `CONFIRMED`, `DISMISSED`, `SUPERSEDED`. A
`NEEDS_REVIEW` alert can be confirmed or dismissed in the UI.

## 6. AI model (Ollama)

- External Ollama service; model `qwen3.6:27b`.
- `.env`: `OLLAMA_BASE_URL`, `OLLAMA_MODEL`; configurable from `.env` and the UI:
  thinking per stage (condition analysis on, observation off, decision on), `num_ctx`
  (32768), timeout (300 s), `keep_alive` (30m), max image side (0 = original).
- **temperature is fixed at 0.0**.
- JSON-schema structured output, validated with Pydantic.
- **Errors**: 3 retries with exponential backoff; invalid JSON → one "fix it" request;
  then the evaluation is `ERROR` and can be re-run. The header shows an **Ollama status**
  light.
- **System prompts** live in `app/prompts/*.md` and are readable on the Settings page.
  Every call stores the full prompt and the raw response (debug view).

## 7. Storage

**events.db** – owned by the camera system; analytics reads it only.

- `sources`: file name (unique), camera id, path, duration, fps, size, upload time.
- `camera_events`: event id, camera id, event number, source, timestamp (display + ISO),
  duration, fps, clip path, trigger-frame path, trigger offset.

**analytics.db**

- `conditions`, `condition_versions`
- `frame_selections` – threshold, mode, selected times and scores, chart signals
- `event_queue`, `episodes`
- `observations`, `runs`, `evaluations`, `alerts`
- `llm_calls` – every model call (prompt, raw response, thinking, timing)
- `jobs` – re-runs and backfills for the worker
- `pipeline_log`, `settings`

Access: plain `sqlite3` + Pydantic. There is no foreign key across the two files.

## 8. Web UI (FastAPI + plain HTML/CSS/vanilla JS, English)

Live updates via **Server-Sent Events** (`/api/stream`). No authentication.

1. **Dashboard** – live pipeline log, worker and Ollama status, queue sizes, latest alerts.
2. **Import** – see 3.
3. **Cameras** – list; detail: prompts (create, edit → new version, enable/disable,
   delete, scope and focus questions), event timeline grouped by episode, per event ×
   prompt the current result and the run history, Re-run (with force re-observe), a
   **switchable MP4 preview / looping preview of the frames the model saw** (the same
   3 s loop as on the import page; click opens the frame in full resolution), the
   read-only selection chart, debug view of prompts and raw answers.
   **Delete camera** (in the list and on the detail page, after a confirmation) removes the
   camera everywhere: its events, source videos, clips and trigger frames from the camera
   system, and its prompts, episodes, observations, decisions, alerts and LLM calls from
   the analytics. Afterwards the camera id and the source file names can be imported
   again (numbering restarts at #001). Refused while the worker is busy with the camera.
   In this prototype the analytics API performs the camera-system part on behalf of the
   simulated backend; the analytics itself still never writes `events.db`.
4. **Alerts** – filterable list; detail with key frame, focus results, confidence,
   reasoning, episode summary, events, confirm / dismiss for `NEEDS_REVIEW`.
5. **Settings** – every editable setting (overrides `.env`, with reset), Ollama connection
   test, system prompts (read-only).

## 9. Technology

Python 3.14, `requirements.txt` (pinned), FastAPI, Uvicorn, Pydantic, httpx, NumPy,
OpenCV (headless), ffmpeg CLI. One container, one process: the FastAPI lifespan starts the
worker task; SSE is an in-memory pub/sub. `docker compose up`, port 8000 (`APP_PORT`),
`./data` mounted.
Ollama runs outside of compose.

## 10. Defaults

| Setting | Value |
|---|---|
| Port | 8000 |
| Episode gap | 15 s |
| Idle close | 30 s |
| Camera memory | 24 h |
| Frames per segment | min 5 / max 30 (frame scope: 3) |
| Signal sampling | 15 fps, 320 px |
| Selection threshold | auto (median + 6·MAD per video) |
| VLM image size | original |
| Retries | 3, exponential |
| num_ctx / timeout / keep_alive | 32768 / 300 s / 30m |
| temperature | 0.0 (fixed) |
| Thinking (condition / observation / decision) | on / off / on |
| Analysis start | auto |

## 11. Demo

All 17 test videos are imported **by hand on the Import page**. `demo/scenarios.json`
maps camera → video(s) → prompt(s) → **accepted outcomes**; `scripts/seed_demo.py` only
creates the prompts. The demo prompts are in English.

Covered required cases: decidable from one frame, needs several events / history,
not enough information (NEEDS_REVIEW), negative (event happens, condition does not).

Camera ids in the demo are the file names (the import default), except **FrontPorch**:
`foodDelivery.mp4` as a new camera, then `FoodRobbery.mp4` added to it 5 minutes later.

## 12. Tests

- **Unit (no model)**: splitting and remainder, timestamps, numbering and duplicate
  rejection, folder layout, change signals and hybrid selection (limits, manual override,
  mandatory frames), MJPEG parsing, episodes, alert reconciliation (state machine),
  read-only access to `events.db`, API.
- **Live (real Ollama, thinking off)**: condition analysis and the demo scenarios end to
  end on the already imported events, against a temporary copy of `analytics.db`, checked
  against the accepted outcomes, with a summary report (`data/reports/`). Skipped when
  Ollama or a camera is missing.

## 13. Assumptions

- Each test video shows one real happening; in production one camera id can have many
  separate episodes – handled by episode grouping.
- Analytics never writes `events.db`.
- Timestamps are simulated; the idle part of episode closing uses the wall clock.
- No audio.

## 14. Out of scope

Real RTSP streams, Kubernetes / multi-host infrastructure, a full CCTV/VMS, authentication.
