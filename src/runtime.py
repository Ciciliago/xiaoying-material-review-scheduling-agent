"""Trusted runtime that enforces ordering, facts, and business constraints."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schemas import Action, SchedulingRequest, Slot
from .store import DataAccessError, DraftStore
from .tools import SchedulingTools


MAX_STEPS = 8
MAX_TOOL_CALLS = 8


def _same_slot(left: Slot, right: Slot) -> bool:
    return left.date == right.date and left.time == right.time


@dataclass
class RuntimeState:
    request: SchedulingRequest
    material: dict[str, Any] | None = None
    material_status: str = "not_checked"
    missing_fields: list[str] = field(default_factory=list)
    rules: dict[str, Any] | None = None
    slot_query: dict[str, Any] | None = None
    save_result: dict[str, Any] | None = None
    terminal_status: str | None = None
    terminal_message: str | None = None
    invalid_actions: list[str] = field(default_factory=list)
    tool_errors: list[dict[str, Any]] = field(default_factory=list)


class AgentRuntime:
    def __init__(self, request: SchedulingRequest, tools: SchedulingTools, store: DraftStore) -> None:
        self.state = RuntimeState(request=request)
        self.tools = tools
        self.store = store
        self.tool_calls = 0

    def public_context(self) -> dict[str, Any]:
        """Facts the model may use; final fields remain runtime-owned."""
        return {
            "request": {
                "request_id": self.state.request.request_id,
                "material_id": self.state.request.material_id,
                "target_date": self.state.request.target_date,
                "requested_time": self.state.request.requested_time,
                "time_constraint": self.state.request.time_constraint,
                "allow_alternative": self.state.request.allow_alternative,
            },
            "terminal": self.state.terminal_status,
        }

    def _reject(self, action: Action, reason: str) -> dict[str, Any]:
        self.state.invalid_actions.append(f"{action.name}: {reason}")
        return {"ok": False, "error": "action_rejected", "action": action.name, "reason": reason}

    def _terminal(self, status: str, message: str) -> None:
        self.state.terminal_status = status
        self.state.terminal_message = message

    def execute(self, action: Action) -> dict[str, Any]:
        if action.name == "finish":
            return self.finish()
        if self.state.terminal_status:
            return self._reject(action, "任务已有终态，只能 finish")
        if action.name not in {"get_material", "check_rules", "query_slots", "save_draft"}:
            return self._reject(action, "未知工具")
        self.tool_calls += 1
        if self.tool_calls > MAX_TOOL_CALLS:
            self._terminal("incomplete", "已达到最大工具调用次数，排期尚未完成。")
            return {"ok": False, "error": "tool_call_limit"}
        try:
            return getattr(self, f"_{action.name}")(action)
        except Exception as error:  # Tool implementations must not terminate the Agent process.
            return self.handle_tool_exception(action, error)

    def handle_tool_exception(self, action: Action, error: Exception) -> dict[str, Any]:
        """Convert a tool failure to a safe observation without bypassing runtime guards."""
        fatal = isinstance(error, (DataAccessError, OSError, ValueError, KeyError, TypeError, AttributeError))
        error_type = getattr(error, "error_type", type(error).__name__)
        safe_message = getattr(error, "safe_message", None)
        if not safe_message:
            safe_message = "工具执行失败。"
        record = {
            "tool": action.name,
            "error_type": error_type,
            "message": safe_message,
            "recoverable": not fatal,
        }
        self.state.tool_errors.append(record)
        if fatal:
            self._terminal("failed", "读取或保存业务数据失败，当前执行已停止。")
        return {"ok": False, "tool": action.name, "error": "tool_execution_error", **record}

    def _get_material(self, action: Action) -> dict[str, Any]:
        if self.state.material_status != "not_checked":
            return self._reject(action, "素材已经读取")
        if action.arguments.get("material_id") != self.state.request.material_id:
            return self._reject(action, "material_id 必须与原请求一致")
        observation = self.tools.get_material(self.state.request.material_id)
        self.state.material_status = observation["status"]
        if observation["status"] == "found":
            self.state.material = observation["material"]
        else:
            self.state.missing_fields = observation.get("missing_fields", [])
            suffix = "素材不存在" if observation["status"] == "not_found" else "素材必要信息不完整"
            self._terminal("blocked", f"{suffix}，请补充后重试。")
        return {"ok": True, "tool": "get_material", "result": observation}

    def _check_rules(self, action: Action) -> dict[str, Any]:
        if self.state.material_status != "found":
            return self._reject(action, "需要先读取完整素材")
        if self.state.rules is not None:
            return self._reject(action, "规则已经检查")
        if action.arguments.get("material_id") != self.state.request.material_id:
            return self._reject(action, "material_id 必须与原请求一致")
        observation = self.tools.check_rules(self.state.request.material_id)
        self.state.rules = observation
        if observation["status"] == "blocked":
            self._terminal("blocked", "素材触发硬性审核规则，无法进入排期。")
        return {"ok": True, "tool": "check_rules", "result": observation}

    def _query_slots(self, action: Action) -> dict[str, Any]:
        if self.state.rules is None or self.state.rules["status"] != "passed":
            return self._reject(action, "需要先通过规则检查")
        if self.state.slot_query is not None:
            return self._reject(action, "当前请求已经查询过时段")
        if not self.state.material:
            return self._reject(action, "缺少可信素材时长")
        expected = {
            "target_date": self.state.request.target_date,
            "time": self.state.request.requested_time,
            "duration_minutes": self.state.material["duration_minutes"],
        }
        if any(action.arguments.get(key) != value for key, value in expected.items()):
            return self._reject(action, "时段查询参数必须与原请求和素材时长一致")
        observation = self.tools.query_slots(**expected)
        self.state.slot_query = observation
        if not observation["available"]:
            request = self.state.request
            if request.time_constraint == "hard":
                self._terminal("blocked", "硬性发布时间已被占用，请用户重新提出发布时间要求。")
            elif not request.allow_alternative or not observation["alternatives"]:
                self._terminal("blocked", "目标时段冲突，当前没有允许且可用的替代时段。")
        return {"ok": True, "tool": "query_slots", "result": observation}

    def _save_draft(self, action: Action) -> dict[str, Any]:
        if self.state.slot_query is None:
            return self._reject(action, "需要先查询时段")
        request = self.state.request
        if action.arguments.get("request_id") != request.request_id or action.arguments.get("material_id") != request.material_id:
            return self._reject(action, "request_id 和 material_id 必须与原请求一致")
        raw_slot = action.arguments.get("proposed_slot")
        if not isinstance(raw_slot, dict) or set(raw_slot) != {"date", "time"}:
            return self._reject(action, "proposed_slot 必须包含 date 和 time")
        proposed = Slot.from_dict(raw_slot)
        requested = request.requested_slot
        query = self.state.slot_query
        if query["available"]:
            allowed = [_same_slot(proposed, requested)]
            status = "ready"
            confirmation_required = False
        else:
            if request.time_constraint == "hard":
                return self._reject(action, "hard 约束下不得提出或保存替代时段")
            if not request.allow_alternative:
                return self._reject(action, "用户未允许替代时段")
            alternatives = [Slot.from_dict(item) for item in query["alternatives"]]
            allowed = [_same_slot(proposed, item) for item in alternatives]
            status = "pending_confirmation"
            confirmation_required = True
        if not any(allowed):
            return self._reject(action, "proposed_slot 未被 query_slots 验证为可用")

        draft = {
            "request_id": request.request_id,
            "material_id": request.material_id,
            "original_request": {
                **request.normalized_payload(),
                "request_id": request.request_id,
            },
            "requested_slot": requested.as_dict(),
            "suggested_slot": proposed.as_dict(),
            "confirmation_required": confirmation_required,
            "status": status,
        }
        observation = self.store.save_draft(draft, self.store.request_hash(request.normalized_payload()))
        self.state.save_result = observation
        if observation["success"]:
            self._terminal(
                "needs_confirmation" if confirmation_required else "completed",
                "替代时段草稿已保存，等待用户确认。" if confirmation_required else "排期草稿已保存。",
            )
        elif observation.get("error") == "idempotency_conflict":
            self._terminal("failed", "request_id 已被不同请求占用，草稿未保存。")
        else:
            self._terminal("failed", "草稿保存失败，请稍后重试。")
        return {"ok": observation["success"], "tool": "save_draft", "result": observation}

    def finish(self) -> dict[str, Any]:
        if not self.state.terminal_status:
            self._terminal("incomplete", "模型在任务完成前结束，排期尚未完成。")
            return {"ok": False, "tool": "finish", "result": "premature_finish"}
        return {"ok": True, "tool": "finish", "result": self.state.terminal_status}

    def incomplete(self, message: str = "已达到最大执行步数，排期尚未完成。") -> None:
        if not self.state.terminal_status:
            self._terminal("incomplete", message)

    def fail(self, message: str) -> None:
        if not self.state.terminal_status:
            self._terminal("failed", message)

    def result(self) -> dict[str, Any]:
        state = self.state
        request = state.request
        rules = state.rules or {"status": "not_checked", "risks": [], "warnings": []}
        query = state.slot_query or {}
        saved = state.save_result or {}
        overall = state.terminal_status or "incomplete"
        draft_status = "not_saved"
        draft_id = None
        draft_error = None
        if state.save_result:
            if saved.get("success"):
                draft_status, draft_id = "saved", saved["draft_id"]
            else:
                draft_status, draft_error = "save_failed", saved.get("error")
                if saved.get("error") == "idempotency_conflict":
                    draft_status = "not_saved"
        suggested: dict[str, str] | None = None
        confirmation_required = False
        confirmation_reason = None
        if saved.get("success"):
            draft = self.store.find_by_request_id(request.request_id)
            if draft:
                suggested = draft["suggested_slot"]
                confirmation_required = bool(draft["confirmation_required"])
                if confirmation_required:
                    confirmation_reason = "原请求时段冲突，替代时段需要用户批准。"
        elif query.get("available"):
            suggested = request.requested_slot.as_dict()
        return {
            "request_id": request.request_id,
            "overall_status": overall,
            "material_id": request.material_id,
            "review_status": "needs_info" if state.material_status in {"not_found", "needs_info"} else rules["status"],
            "risk_reasons": rules.get("risks", []),
            "warnings": rules.get("warnings", []),
            "missing_fields": state.missing_fields,
            "requested_slot": request.requested_slot.as_dict(),
            "suggested_slot": suggested,
            "conflict_reason": query.get("conflict_reason"),
            "confirmation_required": confirmation_required,
            "confirmation_reason": confirmation_reason,
            "draft_status": draft_status,
            "draft_id": draft_id,
            "draft_error": draft_error,
            "publication_status": "not_published",
            "message": state.terminal_message or "排期尚未完成。",
        }
