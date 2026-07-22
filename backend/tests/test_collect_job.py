"""Collect job progress helpers."""

from unittest.mock import patch

from app.services import collect_job


def test_start_collect_job_sets_running_and_finishes():
    # Reset module state
    with collect_job._lock:
        collect_job._state.update(
            {
                "running": False,
                "started_at": None,
                "finished_at": None,
                "current_source": None,
                "message": "Idle",
                "percent": 0,
                "sources": [],
                "ok": 0,
                "total": 0,
                "error": None,
            }
        )

    def fake_run(source: str):
        return {"source": source, "success": True, "found": 3}

    with patch("app.services.collect_job.run_collector", side_effect=fake_run):
        status = collect_job.start_collect_job()
        # Job may still be running or already finished on a fast machine.
        assert status["running"] is True or status.get("percent") == 100
        import time

        for _ in range(100):
            status = collect_job.get_collect_status()
            if not status["running"]:
                break
            time.sleep(0.05)

    status = collect_job.get_collect_status()
    assert status["running"] is False
    assert status["percent"] == 100
    assert status["ok"] == status["total"]
    assert all(s["status"] == "done" for s in status["sources"])
