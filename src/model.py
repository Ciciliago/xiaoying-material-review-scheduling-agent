"""Model adapters: deterministic offline mock and optional live tool calling."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .schemas import Action, SchedulingRequest


class ModelError(RuntimeError):
    pass


class DecisionModel(Protocol):
    name: str
    version: str

    def next_action(self, request: SchedulingRequest, observations: list[dict[str, Any]]) -> Action: ...


@dataclass
class MockDecisionModel:
    """Offline model substitute. It is intentionally labelled mock in every trace."""

    name: str = "deterministic-mock"
    version: str = "1.0"

    def next_action(self, request: SchedulingRequest, observations: list[dict[str, Any]]) -> Action:
        if not observations:
            return Action("get_material", {"material_id": request.material_id}, "先读取素材并确认元数据完整。")
        latest = observations[-1]
        result = latest.get("result", {})
        tool = latest.get("tool")
        if tool == "get_material":
            if result.get("status") != "found":
                return Action("finish", {}, "素材不存在或信息不完整，停止排期。")
            return Action("check_rules", {"material_id": request.material_id}, "素材完整，继续检查审核规则。")
        if tool == "check_rules":
            if result.get("status") == "blocked":
                return Action("finish", {}, "规则阻断，不能进入排期。")
            duration = 60
            # get_material observation is retained, so duration comes from an observed tool result.
            for item in reversed(observations):
                if item.get("tool") == "get_material":
                    duration = item["result"]["material"]["duration_minutes"]
                    break
            return Action(
                "query_slots",
                {"target_date": request.target_date, "time": request.requested_time, "duration_minutes": duration},
                "审核通过，查询用户要求的时段。",
            )
        if tool == "query_slots":
            if result.get("available"):
                proposed = result["requested_slot"]
                return Action(
                    "save_draft",
                    {"request_id": request.request_id, "material_id": request.material_id, "proposed_slot": proposed},
                    "原时段可用，保存草稿。",
                )
            if request.time_constraint == "hard" or not request.allow_alternative or not result.get("alternatives"):
                return Action("finish", {}, "没有可自动保存的合规时段，等待用户处理。")
            return Action(
                "save_draft",
                {"request_id": request.request_id, "material_id": request.material_id, "proposed_slot": result["alternatives"][0]},
                "原时段冲突，保存第一个已验证替代时段并等待确认。",
            )
        if tool == "save_draft":
            return Action("finish", {}, "保存结果已返回，准确结束任务。")
        return Action("finish", {}, "没有可继续的合规动作。")


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_material",
            "description": "读取指定素材及元数据。",
            "parameters": {"type": "object", "properties": {"material_id": {"type": "string"}}, "required": ["material_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_rules",
            "description": "检查素材的硬性审核规则和 warning。",
            "parameters": {"type": "object", "properties": {"material_id": {"type": "string"}}, "required": ["material_id"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_slots",
            "description": "查询请求时段及已验证的替代时段。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_date": {"type": "string"},
                    "time": {"type": "string"},
                    "duration_minutes": {"type": "integer"},
                },
                "required": ["target_date", "time", "duration_minutes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_draft",
            "description": "保存已经验证的时段为草稿；不能发布。",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string"},
                    "material_id": {"type": "string"},
                    "proposed_slot": {
                        "type": "object",
                        "properties": {"date": {"type": "string"}, "time": {"type": "string"}},
                        "required": ["date", "time"],
                    },
                },
                "required": ["request_id", "material_id", "proposed_slot"],
            },
        },
    },
]


@dataclass
class LiveOpenAICompatibleModel:
    api_key: str
    model: str
    base_url: str = "https://api.openai.com/v1"
    name: str = "openai-compatible"
    version: str = "unknown"

    @classmethod
    def from_environment(cls) -> "LiveOpenAICompatibleModel":
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise ModelError("缺少 OPENAI_API_KEY；请使用 mock 模式或配置 live 模式。")
        return cls(
            api_key=key,
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            version=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        )

    def next_action(self, request: SchedulingRequest, observations: list[dict[str, Any]]) -> Action:
        system = (
            "你是素材审核排期 Agent。每次只能选择一个工具，依据已提供的 Observation 决定下一步。"
            "绝不发布。hard 时间冲突时不得提出替代时段。仅在任务已有明确终态时输出 FINISH: 加一句简短依据。"
        )
        user = {
            "request": {
                "request_id": request.request_id,
                "material_id": request.material_id,
                "target_date": request.target_date,
                "requested_time": request.requested_time,
                "time_constraint": request.time_constraint,
                "allow_alternative": request.allow_alternative,
            },
            "observations": observations,
        }
        body = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}],
                "tools": TOOL_DEFINITIONS,
                "tool_choice": "auto",
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request_obj = Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request_obj, timeout=45) as response:
                payload = json.load(response)
        except (HTTPError, URLError, TimeoutError) as error:
            raise ModelError(f"模型调用失败：{error}") from error
        message = payload["choices"][0]["message"]
        calls = message.get("tool_calls", [])
        if calls:
            call = calls[0]["function"]
            try:
                args = json.loads(call["arguments"])
            except json.JSONDecodeError as error:
                raise ModelError("模型返回的工具参数不是合法 JSON") from error
            return Action(call["name"], args, "模型根据最新工具结果选择该动作。")
        content = (message.get("content") or "任务已结束。").strip()
        return Action("finish", {}, content[:200])
