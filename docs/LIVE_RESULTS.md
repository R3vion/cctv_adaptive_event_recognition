# Live demo results

First full run of `pytest tests/live` on all 17 demo videos – real `qwen3.6:27b` on Ollama,
**thinking off** for every call (the test setting), temporary `analytics.db`,
2026-10-30, 18.5 minutes for 49 events.

**14 / 19** prompt–episode outcomes were inside the accepted set of `demo/scenarios.json`.

| Camera | Case | Prompt | Scope | Episode | Accepted | Got | Conf. | OK |
|---|---|---|---|---|---|---|---|---|
| LivingRoomSmoke | single-frame | Alert me if you see smoke. | frame | #1 (7 ev) | ALERT | **NONE** | – | ❌ |
| CarFireAtGasStation | single-frame | Alert me if you see fire. | frame | #1 (4 ev) | ALERT | **ALERT** | 1.00 | ✅ |
| MetalForgeBlast | single-frame | Alert me if there is an explosion or fire. | frame | #1 (4 ev) | ALERT | **ALERT** | 1.00 | ✅ |
| WarehouseWorkers | negative | Alert me if you see smoke. | frame | #1 (2 ev) | NONE | **NONE** | – | ✅ |
| PursuitAndFall | event | Alert me if someone falls to the ground. | event | #1 (1 ev) | ALERT | **ALERT** | 0.95 | ✅ |
| StairwellEscape | event | Alert me if people are fleeing or running away. | event | #1 (4 ev) | ALERT | **ALERT** | 0.95 | ✅ |
| ShopRobbery | event | Alert me if there is an armed robbery. | event | #1 (3 ev) | ALERT | **ALERT** | 0.98 | ✅ |
| StoreInterior | negative | Alert me if there is an armed robbery. | event | #1 (8 ev) | NONE | **ALERT** | 0.95 | ❌ |
| PeopleEnteringHouse | uncertain | Alert me if someone breaks into the house. | event | #1 (2 ev) | NEEDS_REVIEW | **NEEDS_REVIEW** | 0.95 | ✅ |
| FedExDeliveryDog | uncertain | Alert me if an animal attacks a person. | event | #1 (1 ev) | NEEDS_REVIEW | **NONE** | – | ❌ |
| WaitingOutside | history | … loiters in front of the entrance … (longer than 30 seconds). | history | #1 (6 ev) | ALERT | **ALERT** | 0.95 | ✅ |
| PackageDeliveryFrontDoor | history | … leaves a package at the door and then walks away. | history | #1 (2 ev) | ALERT | **ALERT** | 0.95 | ✅ |
| PackageDelivery | uncertain | … leaves a package at the door and then walks away. | history | #1 (1 ev) | NEEDS_REVIEW | **NONE** | – | ❌ |
| FedExFlyingDelivery | history | … leaves a package at the door and then walks away. | history | #1 (1 ev) | ALERT / NEEDS_REVIEW | **NONE** | – | ❌ |
| FoodDeliveryPickup | negative | … leaves a package at the door and then walks away. | history | #1 (2 ev) | NONE | **NONE** | – | ✅ |
| FrontPorch | history | … leaves a package at the door and then walks away. | history | #1 (1 ev) | ALERT | **ALERT** | 0.95 | ✅ |
| FrontPorch | history | … leaves a package at the door and then walks away. | history | #2 (1 ev) | NONE / NEEDS_REVIEW | **NONE** | – | ✅ |
| FrontPorch | history | … takes away a package that was delivered earlier. | history | #1 (1 ev) | NONE | **NONE** | – | ✅ |
| FrontPorch | history | … takes away a package that was delivered earlier. | history | #2 (1 ev) | ALERT | **ALERT** | 0.95 | ✅ |

Every prompt got the expected scope from the condition analysis.

## What went wrong, and where

The pipeline did its part in all five cases (frames, observation, decision, alert
derivation); the differences come from what the model saw or from our own assumptions.

| Case | What happened | Where |
|---|---|---|
| LivingRoomSmoke | The smoke is a slow grey darkening under the ceiling. Within one event (3 frames for a frame-scope prompt) it looks like the clear room; the model reports "air clear" in all 7 events. The change is only visible compared with the start of the episode. | model / design: no reference to the earlier, clear state |
| FedExFlyingDelivery | The adaptive selection **did** catch the throw (frames at 5.67–6.33 s show the package in the air), but the model does not recognise the small, blurred object at night and reports a parked truck. | model (small object) |
| StoreInterior | A guard stretches his arm towards the door; the observation reads it as "holding a long object that resembles a firearm" and the decision says YES (0.95). | model – false positive with high confidence |
| FedExDeliveryDog | The observation misreads the scene ("the driver holds the dog in his arms"), so the decision is NO instead of NEEDS_REVIEW. | model |
| PackageDelivery | The model reports that no package is left – the courier only reaches towards the camera. This matches the video description; our accepted outcome (NEEDS_REVIEW) was a guess. | expectation |
