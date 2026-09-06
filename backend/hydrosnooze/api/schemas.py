"""Turning domain objects into the shapes frontend/src/types.ts expects.

Kept in one place so a field can never drift between the two halves without it
being obvious here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..models import DeviceState, Schedule


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def state_json(state: DeviceState) -> dict[str, Any]:
    return {
        "power": state.power.value,
        "in_schedule": state.in_schedule.value,
        "assumed_mode": state.assumed_mode.value if state.assumed_mode else None,
        "assumed_target_c": state.assumed_target_c,
        "observed_power_w": state.observed_power_w,
        "inferred_activity": state.inferred_activity.value,
        "last_command_at": _iso(state.last_command_at),
        "last_error": state.last_error,
    }


def schedule_json(schedule: Schedule) -> dict[str, Any]:
    return {
        "id": schedule.id,
        "name": schedule.name,
        "enabled": schedule.enabled,
        "days_of_week": schedule.days_of_week,
        "wake_time": schedule.wake_time.strftime("%H:%M"),
        "phase1_temp_c": schedule.phase1_temp_c,
        "phase2_temp_c": schedule.phase2_temp_c,
        "phase3_temp_c": schedule.phase3_temp_c,
        "mode": schedule.mode.value,
        "precool_enabled": schedule.precool_enabled,
        "precondition": schedule.precondition.value,
        "precool_lead_minutes": schedule.precool_lead_minutes,
        # Sent so the app can disable pre-heating with a reason rather than
        # offering a setting that cannot work.
        "preheat_is_possible": schedule.preheat_is_possible,
        "last_written_at": _iso(schedule.last_written_at),
        "updated_at": _iso(schedule.updated_at),
    }
