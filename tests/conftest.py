from __future__ import annotations

from pathlib import Path

import pytest

from src.agent import SchedulingAgent
from src.model import MockDecisionModel
from src.store import DraftStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def make_agent(tmp_path):
    def factory(*, model=None, fail_saves=False, max_steps=8):
        store = DraftStore(PROJECT_ROOT / "data", tmp_path / "drafts.json", fail_saves=fail_saves)
        return SchedulingAgent(model or MockDecisionModel(), store, mode="mock", max_steps=max_steps), store

    return factory


def request(**overrides):
    base = {
        "request_id": "REQ-TEST-001",
        "material_id": "M01",
        "target_date": "2026-11-11",
        "requested_time": "18:00",
        "time_constraint": "flexible",
        "allow_alternative": True,
        "user_note": "pytest",
    }
    base.update(overrides)
    return base
