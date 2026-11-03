# Design notes

Short version first: the important decisions, the alternatives that were weighed, and
what would change in production. The complete decision log is in the
[appendix](#appendix--full-decision-log).

The design was worked out in a question-and-answer process between the author and an AI
assistant (see [AI_USAGE.md](AI_USAGE.md)). The *Origin* column of the log records who
made each call: **Author** = the author's own idea or choice, often against the
assistant's recommendation; **AI → accepted** = the assistant's recommendation, accepted
by the author; **AI idea → accepted** = a question or mechanism the assistant raised.

---

## 1. Key decisions

### Two-step evaluation instead of "ask the VLM yes/no"
A prompt is analysed once into a *ConditionSpec* (scope, focus questions, criteria). Per
event one **shared, focused observation** call looks at the frames, then a cheap
**text-only decision** runs per prompt. *Alternatives*: one VLM call per event × prompt
deciding directly (simpler, but N× image cost and hard to debug); a rule engine over VLM
labels (deterministic, but cannot follow free-text prompts).

### Scope: frame / event / history
Some prompts are decidable from one image (smoke), some need motion within 10 s (falling),
some need the course of several events (loitering, package left behind). The LLM
proposes the scope, the user can override it. The scope drives how many frames are sent
and what context the decision gets.

### Episodes from timestamps, camera memory from summaries
Consecutive events closer than 15 s form an **episode** (one real happening). History
prompts see **every observation of the episode** and short **summaries of earlier
episodes** of the same camera (24 h) – this is how "someone takes away the package that
was delivered 5 minutes ago" becomes decidable. *Alternatives*: VLM-confirmed grouping,
a plain sliding window.

### Alerts are derived, runs are kept
Every evaluation belongs to a run; re-running a history prompt **replays the episode
causally** (each event only sees earlier ones). Alerts are recomputed from the current
evaluations, so re-runs never duplicate alerts; replaced alerts become `SUPERSEDED`.
The first `YES` stops the prompt for the rest of the episode (author's choice: cheaper,
one alert per happening).

### Uncertainty is a first-class outcome
`UNCERTAIN` / an unresolved `PENDING` does not alert immediately: at episode close a
closing decision runs, and if it is still unclear a `NEEDS_REVIEW` alert is created for a
human.

### Adaptive frame selection (the author's design)
Uniform sampling misses fast events (a package thrown in half a second) and does not
"see" slow ones (smoke creeping down). Frames are chosen from a **change signal**
(changed-pixel ratio + colour-histogram distance, optionally optical flow) measured
against both the **previous frame** and the **last keyframe**, with a hybrid algorithm
(mandatory frames, keyframes, peaks, NMS, cumulative fill) between **5 and 30 frames**.
The import page shows the curve per segment with a **draggable threshold**; a manual
threshold overrides the limits.

### Two SQLite files (the author's design)
The camera system's database (`events.db`) is treated as foreign: the analytics opens it
read-only and keeps everything in `analytics.db`. Rationale (author): the task only says
the application may *access* that database, not write it. Trade-off: no cross-file
foreign keys.

### Simple, inspectable stack
One container and one process (FastAPI + an asyncio worker), SQLite with plain `sqlite3`,
plain HTML/CSS/JS with SSE, ffmpeg CLI. Every LLM call is stored with its prompt and raw
answer; system prompts are files you can read in the UI.

### Images fit the context window
A 1080p frame costs ~1.5k tokens, so 30 originals would not fit a 32k context. Frames go out
in original resolution when they fit; otherwise all images of that call are scaled down by
the same factor. The debug view shows the size each image was sent in.

## 2. Measured on the development setup

`qwen3.6:27b` on the author's Ollama server (≈80 tokens/s):

| Step | Typical time |
|---|---|
| Import analysis of an 80 s 1080p video (signals for all frames) | ~4.5 s |
| Condition analysis (thinking on / off) | ~35 s / ~5 s |
| Observation, 5–25 frames (thinking off) | 20–36 s (9k–24k prompt tokens) |
| Decision (thinking on / off) | ~36 s (≈2.4k thinking tokens) / a few s |
| Episode summary | ~2 s |

Thinking on decisions roughly doubles the time per event; it can be switched off on the
Settings page.

## 3. Accepted risks

- **No context limit** on history decisions: very long episodes may
  exceed `num_ctx` or slow down.
- **Stop after first YES**: later evidence of the same episode is not added to the alert.
- **Serial processing**: one worker, one event at a time – fine for a demo, not for a
  fleet of cameras.
- **Idle close uses the wall clock** while timestamps are simulated.
- **Live tests are not deterministic**: mitigated by temperature 0.0 and *sets* of
  accepted outcomes.

## 4. What would change in production

- **Storage and queueing**: PostgreSQL; a message queue (Redis Streams / Kafka) instead of
  polling; horizontally scaled workers partitioned by camera.
- **Context management**: compress older observations or keep a structured episode state
  above a limit; vector search over camera memory.
- **Tracking**: object tracking and re-identification to link episodes, not only time gaps.
- **Throughput**: GPU batching, a small pre-filter model in front of the 27B VLM, tuning
  frame selection on labelled data.
- **Quality**: a labelled evaluation set, confidence calibration, regression tests for the
  system prompts.
- **Security**: authentication and roles, prompt-injection hardening of user prompts,
  audit log; Ollama on an internal network that only the analytics service can reach
  (today the browser already never calls or sees it).
- **Operations**: metrics and tracing, alert delivery (webhook / e-mail / VMS), retention
  policies, time zones.
- **Input**: a real ingest API or RTSP instead of the import page.

---

## Appendix – full decision log

| # | Topic | Alternatives weighed | Decision | Origin |
|---|---|---|---|---|
| 1 | Evaluation principle | single-step VLM decision; rules over labels | Hybrid two-step (condition analysis → observation → decision) | AI → accepted |
| 2 | Database | PostgreSQL | SQLite | Author |
| 3 | Database layout | one file, separate tables | Two files: `events.db` (read-only for analytics) + `analytics.db` | Author |
| 4 | DB access | SQLAlchemy, SQLModel | Plain `sqlite3` + Pydantic | Author |
| 5 | UI technology | Jinja2 + HTMX, React SPA | FastAPI + plain HTML/CSS/JS, kept simple | Author |
| 6 | Ollama | in compose (profile), mock mode | External, configured in `.env` | Author |
| 7 | Observation with several prompts | one VLM call per prompt; generic description only | One shared, focused VLM call per event | AI → accepted |
| 8 | Episode grouping | gap + VLM confirmation; sliding window | Timestamp gap, configurable | AI → accepted |
| 9 | Event source | scenario file; ingest API | Web import page with the full metadata spec (ids, camera = file name, timestamp format, today/random, 10 s split, trigger slider, folders, save) | Author |
| 10 | Folder layout | flat with numbered frames | One sub-folder per chunk | AI → accepted |
| 11 | Re-upload with same name | overwrite; continue numbering | Reject (later refined: reject the same *source file name*) | Author, refined together (#40) |
| 12 | Remainder segment | keep if ≥ 3 s; append to previous | Always keep | Author |
| 13 | Starting analysis | auto only; manual only | Both, switchable, plus re-run | Author |
| 14 | Start timestamp | midnight; manual only | Upload time / random date + time | AI → accepted |
| 15 | Trigger frame picker | one slider for the whole video | Segment cards with own slider | AI → accepted |
| 16 | Prompts & history | – | Prompt–camera storage, per event × prompt results with history, re-run button per event; author raised the history re-run problem | Author |
| 17 | Re-run of history prompts | all camera events; hindsight with full context | Causal episode replay in a new run, prompt versions, superseded results | AI → accepted (refinement of #16) |
| 18 | Prompts per camera | single active prompt | Several, individually switchable | AI → accepted |
| 19 | Observation cache | always fresh VLM call | Reuse when frames and questions match; force option | AI → accepted |
| 20 | Who sets the scope | LLM only; user only | LLM proposes, user can override | AI → accepted |
| 21 | Alert content | entity list | Confidence + reasoning, episode summary, key frame, **plus what triggered the alert and which focus questions were met** | Author |
| 22 | Key frame | bounding box; always trigger frame | Model picks the frame, no box | Author |
| 23 | Thinking mode | everywhere on / off | Off for observation, on for analysis and decisions, switchable | AI → accepted |
| 24 | Error handling | pause worker | Retry → ERROR, Ollama status light | AI → accepted |
| 25 | UNCERTAIN | log only; immediate low-confidence alert | Wait for episode end → NEEDS_REVIEW | AI → accepted |
| 26 | Pages | single page with tabs | Five pages | AI → accepted |
| 27 | Settings storage | `.env` only | `.env` defaults + UI overrides in DB | AI → accepted |
| 28 | Language | mixed | Everything in English | Author |
| 29 | Clip playback | MJPEG stream; MP4 only; gallery only | Switchable MP4 preview ↔ frame gallery (original clip vs. what the model saw, for debugging) | Author |
| 30 | MP4 preview | written at import into the chunk folder | Generated on demand on the analytics side, cached | AI → accepted |
| 31 | Video tooling | OpenCV, PyAV | ffmpeg CLI (+ OpenCV for signals) | AI → accepted |
| 32 | Live updates | WebSocket, polling | SSE | AI → accepted |
| 33 | Processes | separate web and worker containers | One container, asyncio worker | AI → accepted |
| 34 | Tests | mocked model client | Real model (thinking off) for AI parts; logic without a model | Author |
| 35 | History context | state + last K; rolling summary | All observations of the episode | Author |
| 36 | Context limit | limit + compression; sliding window | No limit | Author |
| 37 | Episode end | only on the next event | Next event with gap **or** idle time | AI → accepted |
| 38 | After a YES | append evidence; alert on every YES | Stop analysing that prompt in the episode | Author |
| 39 | De-duplication | history scope only | Per episode for all scopes | AI → accepted |
| 40 | Camera vs. event identity | numbering restarts per video | Editable camera id, pick existing camera, per-camera running `<camera>#NNN`, reject duplicate source file names; author raised the production problem (one camera, several separate happenings) | Author |
| 41 | Using earlier episodes | within episode only | Camera memory from episode summaries (24 h) | AI idea → accepted |
| 42 | Closing decision for history scope | – | Closing decision call at episode end | AI idea → accepted |
| 43 | Demo provisioning | scripted import | Manual import in the UI; the seed script only creates prompts | Author |
| 44 | Demo mapping | – | 17 videos → cameras/prompts/accepted outcomes; FrontPorch: two videos 5 min apart | AI idea → accepted |
| 45 | Live test strictness | exactly one expected outcome | Set of accepted outcomes + report | AI → accepted |
| 46 | Authentication | Basic Auth; login page | None | Author |
| 47 | System diagram | Mermaid only; image only | Mermaid + SVG | AI → accepted |
| 48 | VLM image size | 768 px; 512 px | Original by default, configurable | Author |
| 49 | Python / packaging | 3.12 with pip / uv / Poetry | Python 3.14 + `requirements.txt` | Author |
| 50 | System prompts | editable in UI; hard-coded | Files, readable in the UI, full debug log | AI → accepted |
| 51 | Configurable Ollama params | – | URL, model, thinking, num_ctx, timeout, keep_alive; temperature fixed at 0.0 | Author |
| 52 | Frame selection principle | uniform sampling by scope (earlier decision) | Adaptive, change-based, compared with already processed frames, with a chart | Author (replaced #earlier) |
| 53 | Change signal | pixel ratio only; optical flow only | Pixel ratio (two references) + histogram, weighted; switchable to optical flow | Author |
| 54 | Selection algorithm | peaks; cumulative; keyframe threshold | Hybrid | AI → accepted |
| 55 | Frame limits | 3/12, 2/8, 4/16 | min 5 / max 30 (from the author's own VLM speed test) | Author |
| 56 | Chart details | trigger marker only | One chart per segment; markers for trigger, first and last; hover thumbnail; filmstrip; draggable threshold | Author |
| 57 | Where recomputation runs | in the browser (JS) | On the server (one implementation) | AI → accepted |
| 58 | When the threshold can change | also later on the event page | Only during import | Author |
| 59 | Manual threshold vs. limits | limits always apply | Manual threshold overrides the limits | Author |
| 60 | Frames for frame-scope prompts | trigger only; full set | Max 3, subset of the full set | AI → accepted |
| 61 | Where the selection is stored | `events.db` | `analytics.db` (follows the author's two-database rule) | Author |
| 62 | Defaults | – | See SPEC §10 | AI → accepted |
| 63 | Default selection threshold | one fixed value from Settings (original plan) | **Auto, per video**: median + 6·MAD of the frame-to-frame change; fixed value still possible. Raised after measuring all 17 videos (a fixed 12 % gave 0 candidates for the night delivery) | Author, after measurements by AI |
| 64 | Images vs. context window | raise num_ctx to 64k; fixed 1024 px | **Per-call auto-fit**: originals when they fit, otherwise every image of the call is scaled by the same factor to ~75 % of num_ctx (measured: ~1 token / 1000 px, 1412×1080 ≈ 1.5k tokens) | Author, after measurements by AI |
| 65 | Demo camera ids | the illustrative names of the demo table | Camera id = file name (the import default, no typing) except **FrontPorch**, which holds two videos | AI (convenience), to be confirmed by the author |
| 66 | Loitering demo prompt | "for a long time" | "… for a long time (longer than 30 seconds)" – the LLM otherwise chose 3 minutes, longer than the 60 s video | Author, after a live run |
| 67 | Host port | fixed 8000 | `APP_PORT` (default 8000) – 8000 was taken on the development machine | AI |
| 68 | Browser ↔ Ollama | a reverse-proxy route `/api/ollama/*` in the backend; an nginx container in front of the app | **No proxy needed – the backend is the only Ollama client.** The browser never gets the address: `OLLAMA_BASE_URL` is `.env`-only and hidden on the Settings page, status responses omit it, error texts are redacted | Author |
| 69 | Queue display | events only (re-run / backfill jobs were invisible – the tile showed 0 while a job ran) | **Events + jobs** in one number, with an "events · jobs · waiting · errors" line; header light shows the same; a `job` message on enqueue refreshes them at once | Author |
| 70 | Prompt added after the events | auto-backfill every new prompt silently; keep the checkbox unticked | **The "Run on existing events after saving" checkbox follows auto mode** (ticked while auto is on, live when the mode changes, a manual choice wins) – auto mode had looked broken because events imported before the first prompt are analysed with no prompt | Author |
