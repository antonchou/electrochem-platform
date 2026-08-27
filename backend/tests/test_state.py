"""ExperimentState pause/resume elapsed (CSV 回放不得跳段)。"""

import asyncio

import pytest

from app.state import ExperimentState


def test_resume_excludes_pause_wall_clock():
    async def scenario() -> None:
        s = ExperimentState()
        await s.start(experiment_db_id=1)
        await asyncio.sleep(0.08)
        before = s.elapsed()
        await s.stop()
        frozen = s.elapsed()
        assert frozen == pytest.approx(before, abs=0.03)
        await asyncio.sleep(0.2)
        assert s.elapsed() == pytest.approx(frozen, abs=0.02)
        ok = await s.resume()
        assert ok is True
        after = s.elapsed()
        assert after == pytest.approx(frozen, abs=0.05)
        assert after < frozen + 0.1

    asyncio.run(scenario())


def test_reset_clears_pause():
    async def scenario() -> None:
        s = ExperimentState()
        await s.start(experiment_db_id=1)
        await s.stop()
        await s.reset()
        assert s.elapsed() == 0.0
        assert s.status == "idle"

    asyncio.run(scenario())
