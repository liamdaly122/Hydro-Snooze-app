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
        "current_stage": state.current_stage.value if state.current_stage else None,
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
        "stages": [
            {
                "stage": s.stage.value,
                "duration_minutes": s.duration_minutes,
                "temp_c": s.temp_c,
                # Derived, not stored: below 25C has to cool because warming
                # cannot express it, and at or above 25C it warms.
                "mode": s.mode(schedule.cooling_speed).value,
            }
            for s in schedule.stages
        ],
        "cooling_speed": schedule.cooling_speed.value,
        "precool_enabled": schedule.precool_enabled,
        "precondition": schedule.precondition.value,
        "precool_lead_minutes": schedule.precool_lead_minutes,
        # Sent so the app can disable pre-heating with a reason rather than
        # offering a setting that cannot work.
        "preheat_is_possible": schedule.preheat_is_possible,
        "updated_at": _iso(schedule.updated_at),
    }
