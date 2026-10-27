# AI usage

## Tool

**Claude (Anthropic), Claude Code desktop app, model Claude Opus 5.5** – used as a design
partner and pair programmer. Commits written with it carry a `Co-Authored-By: Claude` line.

## How the work was split

### Design – led by the author
The author pasted the assignment and asked the assistant to *plan together*, explicitly to
be asked about every decision before any code was written. The assistant's job in this
phase was to ask, to list alternatives with their trade-offs and to point out conflicts;
the author made the calls – about 60 of them, recorded in
[DESIGN_NOTES.md](DESIGN_NOTES.md#appendix--full-decision-log) with who decided what.

Many answers were not picks from a list but the author's own designs written out in full,
often against the assistant's recommendation:

- the whole **import page / camera-system simulation**: event ids, camera id = file name,
  timestamp format and the today/random switch, 10-second chunks with the remainder kept,
  the trigger-frame slider, the folder layout, saving everything on *Save*;
- **two separate databases** – the author did not want to assume write access to the camera
  system's database;
- raising the **history re-run problem** (what does re-running one event mean for a prompt
  that spans several events?) and the **camera-id vs. event-id problem** for production
  (one camera, several unrelated happenings);
- **adaptive, change-based frame selection** compared with already processed frames, with a
  per-segment chart, markers for trigger/first/last, a filmstrip, a **draggable threshold**
  that overrides the limits, and the **5–30 frame limits** taken from the author's own speed
  test of the VLM;
- alert content (which focus questions were met, the key frame as a priority), the
  switchable MP4 / frame-gallery view for debugging, stopping a prompt after its first YES,
  no context limit, original image resolution, Python 3.14, English everywhere, no auth,
  temperature 0.0, manual demo import, real-model tests.

The full specification ([SPEC.md](SPEC.md)) and the design notes were written by the
assistant from these decisions and **reviewed and approved by the author before coding
started**.

### Decisions that came from measurements during implementation
The assistant measured on the author's videos and Ollama server and brought the numbers
back; the author decided:

- a fixed selection threshold failed on the test set (0 candidate frames for the night
  delivery) → the author chose a **noise-adaptive auto threshold**;
- 30 frames in original resolution need ~45k tokens, more than `num_ctx` → the author chose
  **per-call image auto-fit** (originals when they fit, uniform downscale otherwise).

### Implementation – written by the assistant
The assistant wrote the code, the system prompts, the tests and the documentation drafts,
milestone by milestone (see the git history), and verified each milestone:

- unit tests (no model) for splitting, timestamps, numbering, frame selection, episodes,
  the alert state machine, read-only access to `events.db`, the API;
- the UI driven in a real browser (import with threshold dragging, camera and alert pages);
- live runs on the author's Ollama server (`qwen3.6:27b`): condition analysis, the full
  pipeline on all 17 demo videos, re-runs and the observation cache;
- the Docker image (tests inside the container, connection to the external Ollama).

The author supplied the test videos with written descriptions, the ffmpeg binaries for
local development, the Ollama server and the GitHub repository.

## What the author checked or changed

- Reviewed and approved the specification and the design notes before implementation.
- Rejected or replaced the assistant's recommendation in the decisions marked *Author* in
  the decision log (e.g. SQLite instead of PostgreSQL, plain JS instead of HTMX, two database
  files, all observations instead of a rolling state, stop after first YES).
- Asked for the auto threshold and the image auto-fit to be explained before accepting them.
- Pointed out that describing the design as "AI-led" would be inaccurate – this document was
  written to reflect that.
- *Further review notes from the author's own testing are added here.*
