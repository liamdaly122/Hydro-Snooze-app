"""Settings, read from the environment or a .env file.

The two lines that matter are `HS_TRANSMITTER` and `HS_POWER_MONITOR`. Today they
say `fake`. When the blaster and the plug arrive they say `esphome` and `shelly`,
and nothing else in this project changes. That is the whole reason the adapters
exist.
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import DEFAULT_MAX_TEMPERATURE_C, Mode, PowerThresholds


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HS_", env_file=".env", extra="ignore")

    # --- The swap ------------------------------------------------------------
    transmitter: Literal["fake", "esphome"] = "fake"
    power_monitor: Literal["fake", "shelly"] = "fake"

    # --- Real hardware, unused until the parts arrive -------------------------
    esphome_host: str = "hydrosnooze-ir.local"
    esphome_port: int = 6053
    esphome_encryption_key: str = ""
    #: Names of the eight learned infrared codes on the ESPHome device, in the
    #: order they were captured. Filled in at step 6 of the roadmap.
    esphome_button_service: str = "send_ir"
    shelly_host: str = "hydrosnooze-plug.local"

    # --- Power thresholds, guesses until the Shelly measures them -------------
    off_threshold_w: float = 5.0
    idle_max_w: float = 60.0
    cooling_max_w: float = 220.0

    # --- Timing, all from Part 3 of the brief ---------------------------------
    command_gap_ms: int = 300
    save_wait_ms: int = 3000
    arm_wait_s: int = 20
    power_settle_s: int = 10

    # --- Safety ---------------------------------------------------------------
    #: A heater capable of 55C under a bed gets a ceiling, enforced at the API.
    max_temperature_c: int = DEFAULT_MAX_TEMPERATURE_C

    # --- Storage --------------------------------------------------------------
    db_path: str = "data/hydrosnooze.db"
    power_sample_seconds: int = 30

    # --- Simulation, only meaningful when the transmitter is fake -------------
    #: How fast simulated time runs. Clamped, because above about 120 the real
    #: time spent doing the work distorts the simulation.
    sim_speed: float = 1.0
    #: Part 5 step 6 of the roadmap: does the wizard's auto-apply timeout fire
    #: from phase 2 and 3, or only from phase 1? Untested on the real unit. Flip
    #: this to see which sequences stop being self-correcting if the answer is no.
    sim_auto_apply_from_any_phase: bool = True

    @property
    def is_simulated(self) -> bool:
        return self.transmitter == "fake"

    @property
    def thresholds(self) -> PowerThresholds:
        return PowerThresholds(
            off_max_w=self.off_threshold_w,
            idle_max_w=self.idle_max_w,
            cooling_max_w=self.cooling_max_w,
        )

    @property
    def command_gap_s(self) -> float:
        return self.command_gap_ms / 1000

    @property
    def save_wait_s(self) -> float:
        return self.save_wait_ms / 1000

    def cap(self, target_c: int, mode: Mode) -> int:
        """The highest temperature allowed in this mode, safety cap included."""
        from .models import range_for

        _, high = range_for(mode)
        return min(high, self.max_temperature_c)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
