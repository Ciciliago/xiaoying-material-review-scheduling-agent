from __future__ import annotations

from src.runtime import AgentRuntime
from src.schemas import Action, SchedulingRequest
from src.tools import SchedulingTools

from .conftest import request


def build_runtime(store, **overrides):
    parsed = SchedulingRequest.parse(request(**overrides))
    return AgentRuntime(parsed, SchedulingTools(store), store)


def test_save_without_query_is_rejected(make_agent):
    _, store = make_agent()
    runtime = build_runtime(store)
    observation = runtime.execute(
        Action("save_draft", {"request_id": "REQ-TEST-001", "material_id": "M01", "proposed_slot": {"date": "2026-11-11", "time": "18:00"}}, "尝试保存")
    )
    assert observation["error"] == "action_rejected"
    assert store.all_drafts() == []


def test_rule_block_prevents_later_save(make_agent):
    _, store = make_agent()
    runtime = build_runtime(store, material_id="M04")
    runtime.execute(Action("get_material", {"material_id": "M04"}, "读取素材"))
    runtime.execute(Action("check_rules", {"material_id": "M04"}, "检查规则"))
    observation = runtime.execute(
        Action("save_draft", {"request_id": "REQ-TEST-001", "material_id": "M04", "proposed_slot": {"date": "2026-11-11", "time": "18:00"}}, "越权保存")
    )
    assert observation["error"] == "action_rejected"
    assert runtime.result()["overall_status"] == "blocked"
    assert store.all_drafts() == []


def test_unqueried_slot_is_rejected_after_slot_lookup(make_agent):
    _, store = make_agent()
    runtime = build_runtime(store)
    runtime.execute(Action("get_material", {"material_id": "M01"}, "读取素材"))
    runtime.execute(Action("check_rules", {"material_id": "M01"}, "检查规则"))
    runtime.execute(Action("query_slots", {"target_date": "2026-11-11", "time": "18:00", "duration_minutes": 60}, "查询时段"))
    observation = runtime.execute(
        Action("save_draft", {"request_id": "REQ-TEST-001", "material_id": "M01", "proposed_slot": {"date": "2026-11-11", "time": "20:00"}}, "保存未查询时段")
    )
    assert observation["error"] == "action_rejected"
    assert store.all_drafts() == []
