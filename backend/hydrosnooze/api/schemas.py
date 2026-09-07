"""Turning domain objects into the shapes frontend/src/types.ts expects.

Kept in one place so a field can never drift between the two halves without it
being obvious here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..models import DeviceState, Schedule, modes_for


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
    pre = schedule.preconditioning
    return {
        "id": schedule.id,
        "name": schedule.name,
        "enabled": schedule.enabled,
        "days_of_week": schedule.days_of_week,
        "wake_time": schedule.wake_time.strftime("%H:%M"),
        "bed_time": schedule.bed_time.strftime("%H:%M"),
        # Derived, and sent so the app never has to work out which side of
        # midnight the night starts on.
        "night_minutes": schedule.night_minutes,
        "stages": [
            {
                "stage": s.stage.value,
                "duration_minutes": s.duration_minutes,
                "temp_c": s.temp_c,
                # Derived, not stored, and derived from the whole night rather
                # than this stage: between 25C and 35C both modes reach the
                # number and the direction of travel is what decides.
                "mode": mode.value,
            }
            for s, mode in zip(
                schedule.stages,
                modes_for(schedule.stages, schedule.cooling_speed),
                strict=True,
            )
        ],
        "cooling_speed": schedule.cooling_speed.value,
        # Not a setting any more. The service decides how the bed gets ready and
        # sends the decision, so the app reports it rather than asking for it.
        "preconditioning": {
            "mode": pre.mode.value if pre.mode else None,
            "lead_minutes": pre.lead_minutes,
            "reason": pre.reason,
        },
        "updated_at": _iso(schedule.updated_at),
    }
