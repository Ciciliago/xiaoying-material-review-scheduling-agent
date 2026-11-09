"""JSON-backed data and durable draft/idempotency storage."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class DataAccessError(RuntimeError):
    """A sanitized, terminal error while reading or writing durable JSON data."""

    def __init__(self, error: Exception) -> None:
        self.error_type = type(error).__name__
        if isinstance(error, json.JSONDecodeError):
            self.safe_message = "数据文件格式损坏。"
        elif isinstance(error, UnicodeDecodeError):
            self.safe_message = "数据文件编码无效。"
        else:
            self.safe_message = "数据文件无法读取或写入。"
        super().__init__(self.safe_message)


class DraftStore:
    def __init__(self, data_dir: Path, draft_path: Path, fail_saves: bool = False) -> None:
        self.data_dir = data_dir
        self.draft_path = draft_path
        self.fail_saves = fail_saves

    @staticmethod
    def request_hash(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            with path.open(encoding="utf-8") as handle:
                return json.load(handle)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as error:
            raise DataAccessError(error) from error

    def _write_json(self, path: Path, payload: Any) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
            temporary.replace(path)
        except OSError as error:
            raise DataAccessError(error) from error

    def materials(self) -> dict[str, dict[str, Any]]:
        return self._read_json(self.data_dir / "materials.json", {})

    def rules(self) -> dict[str, dict[str, Any]]:
        return self._read_json(self.data_dir / "rules.json", {})

    def schedule(self) -> list[dict[str, Any]]:
        return self._read_json(self.data_dir / "schedule.json", [])

    def _draft_state(self) -> dict[str, Any]:
        return self._read_json(self.draft_path, {"drafts": [], "request_index": {}})

    def find_by_request_id(self, request_id: str) -> dict[str, Any] | None:
        state = self._draft_state()
        reference = state.get("request_index", {}).get(request_id)
        if not reference:
            return None
        return next((draft for draft in state.get("drafts", []) if draft["draft_id"] == reference["draft_id"]), None)

    def save_draft(self, draft: dict[str, Any], request_hash: str) -> dict[str, Any]:
        state = self._draft_state()
        index = state.setdefault("request_index", {})
        existing_ref = index.get(draft["request_id"])
        if existing_ref:
            if existing_ref["request_hash"] != request_hash:
                return {"success": False, "error": "idempotency_conflict", "created": False}
            existing = next(item for item in state["drafts"] if item["draft_id"] == existing_ref["draft_id"])
            return {"success": True, "draft_id": existing["draft_id"], "created": False, "status": existing["status"]}
        if self.fail_saves:
            return {"success": False, "error": "simulated_save_failure", "created": False}

        draft_id = f"DRAFT-{len(state.setdefault('drafts', [])) + 1:03d}"
        persisted = {**draft, "draft_id": draft_id, "request_hash": request_hash}
        state["drafts"].append(persisted)
        index[draft["request_id"]] = {"draft_id": draft_id, "request_hash": request_hash}
        self._write_json(self.draft_path, state)
        return {"success": True, "draft_id": draft_id, "created": True, "status": draft["status"]}

    def all_drafts(self) -> list[dict[str, Any]]:
        return self._draft_state().get("drafts", [])
