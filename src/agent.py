"""Generic ReAct loop: model selects actions, runtime executes and validates them."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .model import DecisionModel, ModelError
from .runtime import AgentRuntime, MAX_STEPS
from .schemas import Action, InputValidationError, SchedulingRequest
from .store import DraftStore
from .tools import SchedulingTools


class SchedulingAgent:
    def __init__(self, model: DecisionModel, store: DraftStore, mode: str = "mock", max_steps: int = MAX_STEPS) -> None:
        self.model = model
        self.store = store
        self.mode = mode
        self.max_steps = max_steps

    def run(self, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            request = SchedulingRequest.parse(payload)
        except InputValidationError as error:
            request_id = payload.get("request_id") if isinstance(payload.get("request_id"), str) else None
            material_id = payload.get("material_id") if isinstance(payload.get("material_id"), str) else None
            result = {
                "request_id": request_id,
                "overall_status": "failed",
                "material_id": material_id,
                "review_status": "not_checked",
                "risk_reasons": [],
                "warnings": [],
                "missing_fields": [],
                "requested_slot": None,
                "suggested_slot": None,
                "conflict_reason": None,
                "confirmation_required": False,
                "confirmation_reason": None,
                "draft_status": "not_saved",
                "draft_id": None,
                "draft_error": None,
                "publication_status": "not_published",
                "message": f"输入校验失败：{'；'.join(error.errors)}",
            }
            return result, self._trace([], result)

        runtime = AgentRuntime(request, SchedulingTools(self.store), self.store)
        try:
            existing = self.store.find_by_request_id(request.request_id)
        except Exception as error:  # Durable idempotency data is part of the Agent boundary.
            action = Action("idempotency_lookup", {"request_id": request.request_id}, "读取既有草稿")
            observation = runtime.handle_tool_exception(action, error)
            result = runtime.result()
            step = {
                "step": 1,
                "action": action.name,
                "arguments": action.arguments,
                "observation": observation,
                "next_action": "end",
                "decision_summary": action.decision_summary,
            }
            return result, self._trace([step], result, runtime.state.invalid_actions, runtime.state.tool_errors)
        if existing and existing.get("request_hash") != self.store.request_hash(request.normalized_payload()):
            result = {
                "request_id": request.request_id,
                "overall_status": "failed",
                "material_id": request.material_id,
                "review_status": "not_checked",
                "risk_reasons": [],
                "warnings": [],
                "missing_fields": [],
                "requested_slot": request.requested_slot.as_dict(),
                "suggested_slot": None,
                "conflict_reason": None,
                "confirmation_required": False,
                "confirmation_reason": None,
                "draft_status": "not_saved",
                "draft_id": None,
                "draft_error": "idempotency_conflict",
                "publication_status": "not_published",
                "message": "request_id 已被不同请求占用，草稿未保存。",
            }
            return result, self._trace([], result)

        observations: list[dict[str, Any]] = []
        steps: list[dict[str, Any]] = []
        for step_number in range(1, self.max_steps + 1):
            try:
                action = self.model.next_action(request, observations)
            except ModelError as error:
                runtime.fail(str(error))
                break
            if steps:
                steps[-1]["next_action"] = action.name
            try:
                observation = runtime.execute(action)
            except Exception as error:  # Last boundary in case a future runtime tool bypasses execute().
                observation = runtime.handle_tool_exception(action, error)
            model_observation = {"tool": action.name, "result": observation.get("result", observation)}
            observations.append(model_observation)
            steps.append(
                {
                    "step": step_number,
                    "action": action.name,
                    "arguments": action.arguments,
                    "observation": model_observation["result"],
                    "next_action": None,
                    "decision_summary": self._decision_summary(action.name, action.decision_summary, observations[:-1]),
                }
            )
            if action.name == "finish":
                steps[-1]["next_action"] = "end"
                break
            if runtime.state.terminal_status == "failed" and observation.get("error") == "tool_execution_error":
                steps[-1]["next_action"] = "end"
                break
        else:
            runtime.incomplete()
        result = runtime.result()
        return result, self._trace(steps, result, runtime.state.invalid_actions, runtime.state.tool_errors)

    @staticmethod
    def _decision_summary(action_name: str, model_summary: str, prior_observations: list[dict[str, Any]]) -> str:
        if model_summary and model_summary != "模型根据最新工具结果选择该动作。":
            return model_summary
        previous = prior_observations[-1] if prior_observations else None
        previous_result = previous.get("result", {}) if previous else {}
        if action_name == "get_material":
            return "开始读取素材，确认必要字段和时长。"
        if action_name == "check_rules":
            return "素材信息完整，继续检查审核规则。"
        if action_name == "query_slots":
            return "审核未阻断，查询用户要求的原始时段。"
        if action_name == "save_draft":
            if isinstance(previous_result, dict) and previous_result.get("available") is False:
                return "原时段冲突，保存工具已验证的替代时段并等待用户批准。"
            return "原请求时段可用，保存草稿。"
        if action_name == "finish":
            return "已获得终态事实，结束本次排期。"
        return "根据最新 Observation 选择下一步。"

    def _trace(
        self,
        steps: list[dict[str, Any]],
        result: dict[str, Any],
        rejected_actions: list[str] | None = None,
        tool_errors: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "run_id": str(uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": self.mode,
            "model": {"name": self.model.name, "version": self.model.version},
            "model_name": self.model.name,
            "model_version": self.model.version,
            "steps": steps,
            "rejected_actions": rejected_actions or [],
            "tool_errors": tool_errors or [],
            "final_status": result["overall_status"],
            "final_result": result,
        }
