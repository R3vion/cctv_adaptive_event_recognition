"""System prompt templates (``app/prompts/*.md``).

Each file has a system part and a user part separated by a line ``---USER---``;
``{{name}}`` placeholders are filled in by the caller.
"""

from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
SEPARATOR = "---USER---"

NAMES = ("condition_analysis", "observation", "decision_single", "decision_history", "decision_closing",
         "episode_summary")


def load(name: str) -> tuple[str, str]:
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    system, _, user = text.partition(SEPARATOR)
    return system.strip(), user.strip()


def render(name: str, **values: object) -> tuple[str, str]:
    system, user = load(name)
    for key, value in values.items():
        user = user.replace("{{" + key + "}}", str(value))
        system = system.replace("{{" + key + "}}", str(value))
    return system, user


def all_templates() -> list[dict]:
    out = []
    for name in NAMES:
        system, user = load(name)
        out.append({"name": name, "system": system, "user": user})
    return out
