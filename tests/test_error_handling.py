from __future__ import annotations

import json

from src.agent import SchedulingAgent
from src.model import MockDecisionModel
from src.store import DraftStore

from .conftest import request


def test_corrupted_materials_returns_structured_failure_and_trace(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "materials.json").write_text('{"M01": ', encoding="utf-8")
    store = DraftStore(data_dir, tmp_path / "drafts.json")
    agent = SchedulingAgent(MockDecisionModel(), store, mode="mock")

    result, trace = agent.run(request())

    assert result["overall_status"] == "failed"
    assert result["draft_status"] == "not_saved"
    assert result["draft_id"] is None
    assert result["publication_status"] == "not_published"
    assert store.all_drafts() == []
    assert [step["action"] for step in trace["steps"]] == ["get_material"]
    assert trace["steps"][0]["observation"]["error"] == "tool_execution_error"
    assert trace["tool_errors"] == [
        {
            "tool": "get_material",
            "error_type": "JSONDecodeError",
            "message": "数据文件格式损坏。",
            "recoverable": False,
        }
    ]


def test_corrupted_materials_does_not_overwrite_existing_draft(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "materials.json").write_text("{invalid", encoding="utf-8")
    draft_path = tmp_path / "drafts.json"
    existing = {"drafts": [{"draft_id": "DRAFT-001", "request_id": "OLD", "status": "ready"}], "request_index": {}}
    draft_path.write_text(json.dumps(existing), encoding="utf-8")
    store = DraftStore(data_dir, draft_path)
    agent = SchedulingAgent(MockDecisionModel(), store, mode="mock")

    result, _ = agent.run(request())

    assert result["overall_status"] == "failed"
    assert json.loads(draft_path.read_text(encoding="utf-8")) == existing
