"""Validated input and small value objects shared by the agent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Any


class InputValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class Slot:
    date: str
    time: str

    def as_dict(self) -> dict[str, str]:
        return {"date": self.date, "time": self.time}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Slot":
        return cls(date=str(value["date"]), time=str(value["time"]))


@dataclass(frozen=True)
class SchedulingRequest:
    request_id: str
    material_id: str
    target_date: str
    requested_time: str
    time_constraint: str
    allow_alternative: bool
    user_note: str = ""

    @property
    def requested_slot(self) -> Slot:
        return Slot(self.target_date, self.requested_time)

    def normalized_payload(self) -> dict[str, Any]:
        """All idempotency-relevant fields, deliberately excluding request_id."""
        return {
            "material_id": self.material_id,
            "target_date": self.target_date,
            "requested_time": self.requested_time,
            "time_constraint": self.time_constraint,
            "allow_alternative": self.allow_alternative,
            "user_note": self.user_note,
        }

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> "SchedulingRequest":
        errors: list[str] = []
        required_strings = ("request_id", "material_id", "target_date", "requested_time", "time_constraint")
        values: dict[str, str] = {}
        for field in required_strings:
            value = payload.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{field} 必须是非空字符串")
            else:
                values[field] = value.strip()

        allow = payload.get("allow_alternative")
        if not isinstance(allow, bool):
            errors.append("allow_alternative 必须是 boolean")

        note = payload.get("user_note", "")
        if not isinstance(note, str):
            errors.append("user_note 必须是字符串")

        if values.get("time_constraint") not in {"hard", "flexible"}:
            errors.append("time_constraint 必须是 hard 或 flexible")

        if "target_date" in values:
            try:
                date.fromisoformat(values["target_date"])
            except ValueError:
                errors.append("target_date 必须为 YYYY-MM-DD")
        if "requested_time" in values:
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", values["requested_time"]):
                errors.append("requested_time 必须为 HH:MM")

        if errors:
            raise InputValidationError(errors)
        return cls(
            request_id=values["request_id"],
            material_id=values["material_id"],
            target_date=values["target_date"],
            requested_time=values["requested_time"],
            time_constraint=values["time_constraint"],
            allow_alternative=allow,
            user_note=note.strip(),
        )


@dataclass(frozen=True)
class Action:
    name: str
    arguments: dict[str, Any]
    decision_summary: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Action":
        return cls(
            name=str(value.get("name", "")),
            arguments=value.get("arguments", {}) if isinstance(value.get("arguments", {}), dict) else {},
            decision_summary=str(value.get("decision_summary", "")),
        )
