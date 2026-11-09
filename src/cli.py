"""CLI entry point for the material review scheduling agent."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .agent import SchedulingAgent
from .model import LiveOpenAICompatibleModel, MockDecisionModel
from .store import DraftStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read_payload(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("输入 JSON 必须是对象")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="素材审核与发布排期 Agent（只保存草稿）")
    parser.add_argument("--input", required=True, type=Path, help="请求 JSON 文件")
    parser.add_argument("--mode", choices=("mock", "live"), default="mock")
    parser.add_argument("--output", type=Path, help="写入最终结构化结果 JSON 文件")
    parser.add_argument("--trace", type=Path, help="写入完整 Trace JSON 文件")
    parser.add_argument("--draft-store", type=Path, default=PROJECT_ROOT / "data" / "drafts.json")
    parser.add_argument("--simulate-save-failure", action="store_true", help="注入保存失败，用于验证")
    parser.add_argument("--max-steps", type=int, default=8)
    args = parser.parse_args()

    if args.max_steps < 1:
        parser.error("--max-steps 必须大于 0")
    payload = _read_payload(args.input)
    model = MockDecisionModel() if args.mode == "mock" else LiveOpenAICompatibleModel.from_environment()
    store = DraftStore(PROJECT_ROOT / "data", args.draft_store, fail_saves=args.simulate_save_failure)
    result, trace = SchedulingAgent(model, store, mode=args.mode, max_steps=args.max_steps).run(payload)
    if args.output:
        _write_json(args.output, result)
    if args.trace:
        _write_json(args.trace, trace)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["overall_status"] not in {"failed", "incomplete"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
