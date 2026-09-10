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

    #: The temperature probe board. Empty host means no probes, which is what
    #: every setup was until they arrived and is still what the tests get.
    #:
    #: Its own key rather than the blaster's: two devices in two parts of the
    #: room doing two jobs should not both open to one leaked string.
    probes_host: str = ""
    probes_port: int = 6053
    probes_encryption_key: str = ""

    # --- Telling someone ------------------------------------------------------
    #: An ntfy.sh topic. Empty means no notifications, which is the default.
    #: The topic name is the only secret there is, so make it long and random:
    #:   HS_NTFY_TOPIC=hydrosnooze-liam-7f3a91c4
    ntfy_topic: str = ""
    ntfy_server: str = "https://ntfy.sh"

    #: A healthchecks.io ping URL, or any URL that notices when it stops being
    #: called. Empty means no heartbeat.
    #:
    #: This is the only alarm that survives the Pi itself dying, because it is
    #: raised by something outside the house rather than by the Pi.
    heartbeat_url: str = ""

    # --- Power thresholds, measured ------------------------------------------
    #
    # Taken off a King HS1001 through a Shelly Plug S Gen3, walking the unit
    # through all four states with the physical remote. 429 settled readings:
    #
    #   off at the wall     1.2 to   1.6 W
    #   on, idle            4.9 to  10.2 W   (5 in warming, 9 in cooling)
    #   cooling           161.1 to 188.3 W
    #   heating           303.7 to 393.4 W
    #
    # Each threshold sits at the midpoint of the gap it separates. Rounded, but
    # every gap is wide enough that the rounding is free.
    #
    # The first of these used to be 5.0, and that was actively dangerous rather
    # than merely wrong. Idling in warming mode draws 4.9 W, so the app read the
    # unit as OFF at a stage boundary and pressed power to "turn it on", which
    # turned it off. Mid-night. Measuring found it; nothing else would have.
    off_threshold_w: float = 3.0
    idle_max_w: float = 85.0
    cooling_max_w: float = 245.0

    # --- Timing, all from Part 3 of the brief ---------------------------------
    command_gap_ms: int = 300
    save_wait_ms: int = 3000
    arm_wait_s: int = 20
    power_settle_s: int = 10

    # --- Safety ---------------------------------------------------------------
    #: The highest temperature the API accepts. Defaults to the unit's own maximum,
    #: so lower it here to put a software ceiling back on an unattended heater.
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
