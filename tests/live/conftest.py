"""Live tests: real Ollama, thinking off, real events from DATA_DIR/events.db (read only).

The analytics side runs on a temporary analytics.db and a temporary folder for alert
images, so the application's own results are never touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings, set_test_overrides

LIVE_SETTINGS = dict(think_condition=False, think_observation=False, think_decision=False,
                     episode_idle_close_s=10_000, analysis_mode="auto")


@pytest.fixture(scope="session")
def live_env(tmp_path_factory):
    from app.llm.ollama_client import client

    status = client.status(force=True)
    if not status["ok"]:
        pytest.skip(f"Ollama not reachable at {status['url']}: {status['error']}")
    if not status["model_available"]:
        pytest.skip(f"model {status['model']} is not available on {status['url']}")
    real_data = get_settings().data_path
    real_analytics = get_settings().analytics_db_path
    tmp: Path = tmp_path_factory.mktemp("live")
    set_test_overrides(data_dir=str(real_data), analytics_db=str(tmp / "analytics.db"),
                       analytics_files_dir=str(tmp / "analytics"), **LIVE_SETTINGS)
    from app.analytics import repo

    repo.init()
    if real_analytics.exists():  # keep the frame selections made on the Import page (manual thresholds)
        with repo.db() as conn:
            conn.execute("ATTACH DATABASE ? AS real", (str(real_analytics),))  # only read below
            conn.execute("INSERT OR IGNORE INTO frame_selections SELECT * FROM real.frame_selections")
            conn.commit()
            conn.execute("DETACH DATABASE real")
    yield {"data": real_data, "tmp": tmp}
    set_test_overrides()


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/live" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.live)
