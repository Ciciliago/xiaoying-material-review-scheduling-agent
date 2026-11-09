"""Deterministic local business tools. They contain no model decisions."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .schemas import Slot
from .store import DraftStore


class SchedulingTools:
    def __init__(self, store: DraftStore) -> None:
        self.store = store

    def get_material(self, material_id: str) -> dict[str, Any]:
        material = self.store.materials().get(material_id)
        if material is None:
            return {"status": "not_found", "material_id": material_id, "missing_fields": []}
        required = material.get("required_fields", ["title", "body", "source", "duration_minutes"])
        missing = [field for field in required if not material.get(field)]
        duration = material.get("duration_minutes")
        if not isinstance(duration, int) or duration <= 0:
            if "duration_minutes" not in missing:
                missing.append("duration_minutes")
        if missing:
            return {"status": "needs_info", "material_id": material_id, "missing_fields": sorted(set(missing))}
        return {"status": "found", "material": material}

    def check_rules(self, material_id: str) -> dict[str, Any]:
        record = self.store.rules().get(material_id, {"risks": [], "warnings": []})
        risks = record.get("risks", [])
        warnings = record.get("warnings", [])
        return {
            "material_id": material_id,
            "status": "blocked" if risks else "passed",
            "risks": risks,
            "warnings": warnings,
        }

    @staticmethod
    def _interval(slot: Slot, duration_minutes: int) -> tuple[datetime, datetime]:
        start = datetime.fromisoformat(f"{slot.date}T{slot.time}")
        return start, start + timedelta(minutes=duration_minutes)

    def _is_available(self, slot: Slot, duration_minutes: int) -> tuple[bool, str | None]:
        requested_start, requested_end = self._interval(slot, duration_minutes)
        for item in self.store.schedule():
            occupied = Slot(date=item["date"], time=item["time"])
            occupied_start, occupied_end = self._interval(occupied, int(item["duration_minutes"]))
            if requested_start < occupied_end and occupied_start < requested_end:
                return False, f"该时段已被素材 {item['material_id']} 占用"
        return True, None

    def query_slots(self, target_date: str, time: str, duration_minutes: int) -> dict[str, Any]:
        requested = Slot(target_date, time)
        available, reason = self._is_available(requested, duration_minutes)
        alternatives: list[dict[str, str]] = []
        if not available:
            start = datetime.fromisoformat(f"{target_date}T{time}")
            for offset in range(1, 5):
                candidate = Slot(start.date().isoformat(), (start + timedelta(hours=offset)).strftime("%H:%M"))
                candidate_available, _ = self._is_available(candidate, duration_minutes)
                if candidate_available:
                    alternatives.append(candidate.as_dict())
                if len(alternatives) == 2:
                    break
        return {
            "requested_slot": requested.as_dict(),
            "available": available,
            "conflict_reason": reason,
            "alternatives": alternatives,
        }
