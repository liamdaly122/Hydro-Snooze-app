"""Turning domain objects into the shapes frontend/src/types.ts expects.

Kept in one place so a field can never drift between the two halves without it
being obvious here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..models import DeviceHealth, DeviceState, LearnedLead, Profile, Schedule, modes_for


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def state_json(state: DeviceState) -> dict[str, Any]:
    return {
        "power": state.power.value,
        "current_stage": state.current_stage.value if state.current_stage else None,
        "rehearsal_ends_at": _iso(state.rehearsal_ends_at),
        "assumed_mode": state.assumed_mode.value if state.assumed_mode else None,
        "assumed_target_c": state.assumed_target_c,
        "observed_power_w": state.observed_power_w,
        "observed_flow_c": state.observed_flow_c,
        "observed_return_c": state.observed_return_c,
        "observed_room_c": state.observed_room_c,
        "inferred_activity": state.inferred_activity.value,
        "last_command_at": _iso(state.last_command_at),
        "last_error": state.last_error,
    }


def schedule_json(
    schedule: Schedule,
    *,
    bed_c: float | None = None,
    learned: LearnedLead | None = None,
) -> dict[str, Any]:
    """The schedule, plus the two things about tonight that are not stored in it.

    `bed_c` is what the hose probes read now and `learned` is what previous
    nights measured. Both change how the bed gets ready, and leaving them out
    used to mean the card described the assumptions while the scheduler ran on
    the real numbers.
    """
    pre = schedule.preconditioning(bed_c, learned)
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


def health_json(devices: list[DeviceHealth]) -> list[dict[str, Any]]:
    """The device bar. One entry per thing that can independently stop working."""
    return [
        {
            "name": d.name,
            "health": d.health.value,
            "detail": d.detail,
            "last_ok_at": _iso(d.last_ok_at),
        }
        for d in devices
    ]


def profile_json(profile: Profile, schedule: Schedule) -> dict[str, Any]:
    """One saved night, and whether it is the one currently running.

    `active` is computed against the schedule rather than stored, so it can never
    be a flag left behind by an edit made afterwards. Change a temperature and the
    profile stops being active, which is the truth.
    """
    return {
        "id": profile.id,
        "name": profile.name,
        "cooling_speed": profile.cooling_speed.value,
        "stages": [
            {
                "stage": s.stage.value,
                "duration_minutes": s.duration_minutes,
                "temp_c": s.temp_c,
                "mode": mode.value,
            }
            for s, mode in zip(
                profile.stages,
                modes_for(profile.stages, profile.cooling_speed),
                strict=True,
            )
        ],
        "active": profile.matches(schedule),
        "created_at": _iso(profile.created_at),
    }
