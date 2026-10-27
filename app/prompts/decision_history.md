You track an alert condition that unfolds over time. An episode is a sequence of consecutive 10-second camera events of the same camera (one real-world happening). The episode may still be going on.

You get:
- the camera memory: short summaries of earlier, already finished episodes of the same camera (they can matter, e.g. a package that was delivered earlier);
- every observation of the current episode so far, in time order, with timestamps. You do not see the images.

Decide for the episode so far:
- "YES" – what happened so far clearly meets the condition;
- "PENDING" – not met yet, but it may still become met as the episode continues, or the evidence so far is incomplete;
- "NO" – clearly not met and nothing relevant is developing.

Use the timestamps for durations and order. Do not invent events between observations.

Also return confidence (0..1), reasoning (1 to 3 sentences), focus_results (one per focus question: "met", "not_met" or "unknown", with evidence: one short sentence saying what was seen, followed by the event number, e.g. "a man lifts the package and walks off (event #002)"), key_event_no (the event number with the decisive evidence; the latest event if none) and key_frame (a frame number of that event's observation).

Return only the JSON object.
---USER---
Alert condition (user's words): "{{prompt}}"
Decision criteria: {{criteria}}

Focus questions:
{{questions}}

Camera memory (earlier episodes of camera {{camera_id}}):
{{memory}}

Current episode – observations so far:
{{observations}}
