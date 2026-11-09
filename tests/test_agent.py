from __future__ import annotations

from src.schemas import Action

from .conftest import request


class ScriptedModel:
    name = "scripted-test-model"
    version = "1.0"

    def __init__(self, actions):
        self.actions = iter(actions)

    def next_action(self, _request, _observations):
        return next(self.actions)


def test_normal_schedule_saves_unpublished_draft(make_agent):
    agent, store = make_agent()
    result, trace = agent.run(request())

    assert result["overall_status"] == "completed"
    assert result["review_status"] == "passed"
    assert result["draft_status"] == "saved"
    assert result["publication_status"] == "not_published"
    assert result["suggested_slot"] == {"date": "2026-11-11", "time": "18:00"}
    assert len(store.all_drafts()) == 1
    assert [step["action"] for step in trace["steps"]] == ["get_material", "check_rules", "query_slots", "save_draft", "finish"]


def test_missing_info_blocks_without_draft(make_agent):
    agent, store = make_agent()
    result, _ = agent.run(request(material_id="M03"))

    assert result["overall_status"] == "blocked"
    assert result["review_status"] == "needs_info"
    assert set(result["missing_fields"]) == {"body", "source"}
    assert result["draft_status"] == "not_saved"
    assert store.all_drafts() == []


def test_rule_risk_blocks_without_slot_query(make_agent):
    agent, store = make_agent()
    result, trace = agent.run(request(material_id="M04"))

    assert result["overall_status"] == "blocked"
    assert result["review_status"] == "blocked"
    assert result["risk_reasons"]
    assert "query_slots" not in [step["action"] for step in trace["steps"]]
    assert store.all_drafts() == []


def test_warning_is_exposed_without_blocking_schedule(make_agent):
    agent, _ = make_agent()
    result, _ = agent.run(request(material_id="M05"))

    assert result["overall_status"] == "completed"
    assert result["review_status"] == "passed"
    assert result["warnings"] == ["活动截止日期需在发布前再次确认。"]


def test_flexible_conflict_saves_pending_confirmation_draft(make_agent):
    agent, store = make_agent()
    result, trace = agent.run(request(target_date="2026-11-10", requested_time="18:00"))

    assert result["overall_status"] == "needs_confirmation"
    assert result["suggested_slot"] == {"date": "2026-11-10", "time": "19:00"}
    assert result["confirmation_required"] is True
    assert result["draft_status"] == "saved"
    assert store.all_drafts()[0]["status"] == "pending_confirmation"
    assert trace["steps"][2]["observation"]["available"] is False


def test_hard_constraint_overrides_allow_alternative(make_agent):
    agent, store = make_agent()
    result, trace = agent.run(
        request(target_date="2026-11-10", requested_time="18:00", time_constraint="hard", allow_alternative=True)
    )

    assert result["overall_status"] == "blocked"
    assert result["suggested_slot"] is None
    assert result["confirmation_required"] is False
    assert result["draft_status"] == "not_saved"
    assert "save_draft" not in [step["action"] for step in trace["steps"]]
    assert store.all_drafts() == []


def test_disallowed_alternative_blocks(make_agent):
    agent, store = make_agent()
    result, _ = agent.run(request(target_date="2026-11-10", requested_time="18:00", allow_alternative=False))

    assert result["overall_status"] == "blocked"
    assert result["draft_status"] == "not_saved"
    assert store.all_drafts() == []


def test_same_request_is_idempotent_across_agent_runs(make_agent):
    agent, store = make_agent()
    first, _ = agent.run(request())
    second, trace = agent.run(request())

    assert first["draft_id"] == second["draft_id"]
    assert second["draft_status"] == "saved"
    assert len(store.all_drafts()) == 1
    save_step = next(step for step in trace["steps"] if step["action"] == "save_draft")
    assert save_step["observation"]["created"] is False


def test_same_request_id_with_different_payload_is_rejected(make_agent):
    agent, store = make_agent()
    agent.run(request())
    result, trace = agent.run(request(target_date="2026-11-12"))

    assert result["overall_status"] == "failed"
    assert result["review_status"] == "not_checked"
    assert result["draft_error"] == "idempotency_conflict"
    assert trace["steps"] == []
    assert len(store.all_drafts()) == 1


def test_save_failure_is_not_reported_as_success(make_agent):
    agent, store = make_agent(fail_saves=True)
    result, _ = agent.run(request())

    assert result["overall_status"] == "failed"
    assert result["draft_status"] == "save_failed"
    assert result["draft_id"] is None
    assert result["publication_status"] == "not_published"
    assert store.all_drafts() == []


def test_model_claiming_success_after_save_failure_cannot_change_facts(make_agent):
    model = ScriptedModel(
        [
            Action("get_material", {"material_id": "M01"}, "读取素材"),
            Action("check_rules", {"material_id": "M01"}, "审核素材"),
            Action("query_slots", {"target_date": "2026-11-11", "time": "18:00", "duration_minutes": 60}, "查询时段"),
            Action(
                "save_draft",
                {"request_id": "REQ-TEST-001", "material_id": "M01", "proposed_slot": {"date": "2026-11-11", "time": "18:00"}},
                "保存草稿",
            ),
            Action("finish", {}, "草稿已经成功保存。"),
        ]
    )
    agent, _ = make_agent(model=model, fail_saves=True)
    result, trace = agent.run(request())

    assert result["overall_status"] == "failed"
    assert result["draft_status"] == "save_failed"
    assert result["draft_id"] is None
    assert trace["steps"][-1]["decision_summary"] == "草稿已经成功保存。"


def test_step_limit_returns_incomplete(make_agent):
    agent, _ = make_agent(max_steps=1)
    result, _ = agent.run(request())

    assert result["overall_status"] == "incomplete"
    assert result["draft_status"] == "not_saved"


def test_invalid_input_fails_before_model_or_tools_run(make_agent):
    agent, _ = make_agent()
    result, trace = agent.run(request(requested_time="18:00:00"))

    assert result["overall_status"] == "failed"
    assert "HH:MM" in result["message"]
    assert trace["steps"] == []
