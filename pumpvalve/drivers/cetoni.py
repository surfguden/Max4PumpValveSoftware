from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import sys
import time
import math
from typing import Any

from ..logging_support import log_call, log_transaction, run_with_timeout
DEFAULT_PUMP_CONFIGURATION_DIR = Path()


class CetoniPumpError(RuntimeError):
    pass


def _normalize_flow_unit(unit: str | None) -> str:
    """Normalize supported microlitre spellings without changing unit semantics.

    The second replacement preserves compatibility with an older settings/text
    encoding that decoded the UTF-8 micro sign as two Latin-1 characters.
    """
    text = (unit or "ul/min").strip().lower()
    return text.replace(chr(0x00C2) + chr(0x00B5), "u").replace(chr(0x00B5), "u")


def _default_sdk_python_path() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "vendor"


QMIX_REQUIRED_DLLS = (
    "labbCAN_Bus_API.dll",
    "labbCAN_Pump_API.dll",
    "labbCAN_Valve_API.dll",  # qmixpump imports qmixvalve
)


def _local_qmixsdk_path() -> Path | None:
    """Find the per-user SDK install via Windows' LOCALAPPDATA variable."""
    if not (local_appdata := os.environ.get("LOCALAPPDATA")):
        return None
    candidate = Path(local_appdata) / "CETONI_SDK"
    if all((candidate / name).is_file() for name in QMIX_REQUIRED_DLLS):
        return candidate
    return None


def _syringe_stroke_mm(volume_ml: float, inner_diameter_mm: float) -> float:
    area_mm2 = 3.141592653589793 * (inner_diameter_mm / 2.0) ** 2
    return volume_ml * 1000.0 / area_mm2


SYRINGE_PRESETS: dict[str, tuple[float, float]] = {
    # Inner diameters are BD syringe spec values (confirmed authoritative:
    # 1mL=4.78mm, 5mL=12.07mm, 10mL=14.5mm) -- Chemyx BD Plastic Syringe
    # reference table (chemyx.com), cross-checked against BD REF
    # 309628/309649/300912 packaging (no provenance for these three values
    # was ever recorded in this repo's own history prior to Session 51;
    # `git log -p` on this file shows only "confirmed authoritative" with
    # no external citation, back to the syringe feature's original
    # introduction -- added now, not re-deriving anything). Stroke length is
    # not an independently-sourced BD spec value -- it is derived here
    # assuming the full nominal volume is delivered over the full piston
    # travel with a cylindrical bore (stroke = volume / cross-sectional
    # area). No authoritative real BD stroke-length figure was available to
    # verify this assumption against.
    "BD 1ml": (4.78, _syringe_stroke_mm(1.0, 4.78)),
    "BD 5ml": (12.07, _syringe_stroke_mm(5.0, 12.07)),
    "BD 10ml": (14.50, _syringe_stroke_mm(10.0, 14.50)),
}

# Conservative app-level hard bounds for Custom syringe geometry -- unlike
# max_flow_rate_ul_min/max_volume_ml (read back from the pump after
# set_syringe_param() below), there is no live device readback for
# inner_diameter_mm/max_piston_stroke_mm themselves, and CETONI's own SDK
# documentation does not state that pump firmware validates them against
# the syringe actually mounted. A wrong (too-large) value here could ask
# set_syringe_param()'s downstream volume/position math to drive the
# piston holder past its real physical travel.
#
# inner_diameter_mm: spans BD's full published 1mL-60mL product line
# (4.78mm-26.72mm, chemyx.com's BD syringe chart -- consistent with the
# three SYRINGE_PRESETS values above, which are the 1/5/10mL points on
# that same range), padded to [1.0, 35.0] for legitimate brand/size
# variation -- this also comfortably matches CETONI's own NEM-B101-02 E
# hardware manual (Section 5.1), which clamps syringe *outer* diameter to
# 6-30mm on this pump module; 35mm is used as this constant's own ceiling
# since inner diameter is always smaller than outer.
#
# max_piston_stroke_mm: NOT a BD-range-derived value (a prior version of
# this comment padded this module's own volume/diameter->stroke formula
# to [10.0, 200.0], which was wrong -- CETONI's Low Pressure Hardware
# Manual, Section 5.1, NEM-B101-02 E, states this specific pump module's
# absolute mechanical piston travel is "up to 65 mm", independent of
# whatever syringe is mounted; a configured stroke above that can command
# the pump past its own real mechanical limit regardless of the syringe's
# own barrel length, which is exactly the ATTENTION warning in that same
# manual section). 65.0 is a real hardware ceiling, not a padded
# BD-range estimate -- unlike inner_diameter_mm above, do not derive this
# bound from syringe presets/volume math.
MIN_SYRINGE_INNER_DIAMETER_MM = 1.0
MAX_SYRINGE_INNER_DIAMETER_MM = 35.0
MIN_SYRINGE_STROKE_MM = 10.0
MAX_SYRINGE_STROKE_MM = 65.0


@dataclass(slots=True)
class CetoniPump:
    """Reusable CETONI pump implementation backed directly by the Qmix SDK."""

    sdk_python_path: Path = field(default_factory=_default_sdk_python_path)
    configuration_path: Path = DEFAULT_PUMP_CONFIGURATION_DIR
    pump_name: str | None = None
    pump_index: int = 0
    # 200 uL/s (2026-08-03): originally confirmed on real hardware via
    # CETONI Elements, but with a different syringe actually configured
    # than the one active by default -- this flat value alone caused real
    # "Value range of parameter exceeded" SDK rejections on Refill/Empty
    # once the smaller default syringe was connected
    # (logs/hardware_transactions.log). Kept as the fallback TARGET (used
    # when the qt_ui.py "Refill/Empty Flow Rate" field's value isn't
    # passed through explicitly -- e.g. the legacy MessageName.CETONI_
    # REFILL/CETONI_EMPTY dispatch path in application.py), but
    # _fill_flow_rate() below now always clamps whatever target is in play
    # to the currently-configured syringe's own live max, so this can
    # never again be requested past what's actually mounted.
    default_fill_flow_rate_ul_min: float | None = 200.0 * 60.0
    reference_move_timeout_s: float = 60.0
    qmixbus: Any = None
    qmixpump: Any = None
    bus: Any = None
    pump: Any = None
    bus_opened: bool = False
    bus_started: bool = False
    initialized: bool = False
    max_flow_rate_ul_min: float | None = None
    max_volume_ml: float | None = None
    cleanup_timeout_s: float = 5.0
    consecutive_init_fault_clears: int = 0

    def _load_sdk(self) -> None:
        if self.qmixbus is not None and self.qmixpump is not None:
            return
        wrapper = self.sdk_python_path / "qmixsdk" / "__init__.py"
        if not wrapper.is_file():
            raise CetoniPumpError(f"Qmix Python wrapper not found: {wrapper}")
        dll_folder = os.environ.get("QMIXSDK")
        if not dll_folder and (local_sdk := _local_qmixsdk_path()) is not None:
            dll_folder = str(local_sdk)
            # The bundled wrapper reads QMIXSDK only once, at import time.
            os.environ["QMIXSDK"] = dll_folder
        if dll_folder:
            missing = [name for name in QMIX_REQUIRED_DLLS if not (Path(dll_folder) / name).is_file()]
            if missing:
                raise CetoniPumpError(
                    f"QMIXSDK points to {dll_folder}, but required Qmix SDK DLLs are missing: "
                    f"{', '.join(missing)}. QMIXSDK must name the folder containing all required DLLs."
                )
        path = str(self.sdk_python_path)
        if path not in sys.path:
            sys.path.insert(0, path)
        try:
            from qmixsdk import qmixbus, qmixpump
        except Exception as exc:
            raise CetoniPumpError(
                f"Could not load Qmix SDK from {self.sdk_python_path}: {exc}. "
                f"QMIXSDK={dll_folder or '<not set>'}. Install the separate Qmix/CETONI SDK "
                "runtime and set QMIXSDK to the folder containing labbCAN_Bus_API.dll, "
                "labbCAN_Pump_API.dll, and labbCAN_Valve_API.dll "
                "(normally %LOCALAPPDATA%\\CETONI_SDK on Windows, "
                "not the QmixElements application folder)."
            ) from exc
        self.qmixbus = qmixbus
        self.qmixpump = qmixpump

    def initialize(self) -> None:
        configuration_path = self.configuration_path
        self._load_sdk()
        self.bus = self.qmixbus.Bus()
        self.bus_opened = False
        self.bus_started = False
        with log_call("pump", "initialize", command=str(configuration_path)) as result:
            try:
                self._initialize_qmix_session(auto_clear_fault=True)
            except Exception as exc:
                try:
                    self.cleanup()
                except Exception as rollback_exc:
                    raise CetoniPumpError(
                        f"Qmix initialization failed: {exc}; rollback close failed: {rollback_exc}"
                    ) from exc
                raise
            result["response"] = f"max_flow_rate_ul_min={self.max_flow_rate_ul_min}, max_volume_ml={self.max_volume_ml}"

    def _auto_clear_fault_on_initialize(self) -> None:
        """Clear the vendor fault latch and record the observed state.

        This is the owner-approved normal Qmix connection policy. It records
        each clear separately from the enclosing initialize transaction so a
        later hardware-log review can distinguish a healthy connection from a
        stale/benign fault latch cleared during connection.
        """
        pump = self._require_pump()
        fault_before_clear = bool(pump.is_in_fault_state())
        if fault_before_clear:
            self.consecutive_init_fault_clears += 1
        else:
            self.consecutive_init_fault_clears = 0
        try:
            pump.clear_fault()
        except Exception as exc:
            log_transaction(
                "pump",
                "auto_clear_fault_on_initialize",
                command={"fault_before_clear": fault_before_clear},
                success=False,
                error=str(exc),
            )
            raise

        fault_after_clear = bool(pump.is_in_fault_state())
        log_transaction(
            "pump",
            "auto_clear_fault_on_initialize",
            command={"fault_before_clear": fault_before_clear},
            response={
                "fault_after_clear": fault_after_clear,
                "consecutive_init_fault_clears": self.consecutive_init_fault_clears,
            },
            success=not fault_after_clear,
            error="fault remained after automatic clear" if fault_after_clear else None,
            evidence_stage="OBSERVED",
        )
        if self.consecutive_init_fault_clears > 1:
            log_transaction(
                "pump",
                "repeated_init_fault_clear_warning",
                command={"consecutive_init_fault_clears": self.consecutive_init_fault_clears},
                response="fault was present on consecutive initialization attempts",
            )

    def clear_fault_and_reinitialize(self) -> None:
        """Compatibility entry point for an explicit reconnect and fault clear.

        Normal initialization now also clears the device fault after starting
        the bus. Both paths retain _enable_pump() as the final verification
        gate, so a fault that remains or immediately relatches still prevents
        the drive from being enabled.
        """
        configuration_path = self.configuration_path
        self._load_sdk()
        self.bus = self.qmixbus.Bus()
        self.bus_opened = False
        self.bus_started = False
        with log_call("pump", "clear_fault_and_reinitialize", command=str(configuration_path)) as result:
            try:
                self._initialize_qmix_session(auto_clear_fault=False)
            except Exception as exc:
                try:
                    self.cleanup()
                except Exception as rollback_exc:
                    raise CetoniPumpError(
                        f"Qmix clear_fault_and_reinitialize failed: {exc}; "
                        f"rollback close failed: {rollback_exc}"
                    ) from exc
                raise
            result["response"] = f"max_flow_rate_ul_min={self.max_flow_rate_ul_min}, max_volume_ml={self.max_volume_ml}"

    def _initialize_qmix_session(self, *, auto_clear_fault: bool) -> None:
        """Open, select, start, validate, and configure the Qmix session.

        The public initialization paths intentionally choose different fault
        policies, but all other session setup must remain identical.
        """
        configuration_path = self.configuration_path
        with log_call("pump", "bus.open", command=str(configuration_path)) as bus_result:
            self.bus.open(str(configuration_path), 0)
            bus_result["response"] = "opened"
        self.bus_opened = True

        self.pump = self.qmixpump.Pump()
        selection = {"pump_name": self.pump_name} if self.pump_name else {"pump_index": self.pump_index}
        with log_call("pump", "select_pump", command=selection) as select_result:
            if self.pump_name:
                self.pump.lookup_by_name(self.pump_name)
            else:
                self.pump.lookup_by_device_index(self.pump_index)
            select_result["response"] = "selected"

        with log_call("pump", "bus.start") as bus_result:
            self.bus.start()
            bus_result["response"] = "started"
        self.bus_started = True

        if auto_clear_fault:
            self._auto_clear_fault_on_initialize()
        elif self.pump.is_in_fault_state():
            self.pump.clear_fault()
        self._require_position_sensing_initialized_before_enable()
        self._enable_pump()
        self.configure_flow_unit("ul/min")
        self.pump.set_volume_unit(self.qmixpump.UnitPrefix.milli, self.qmixpump.VolumeUnit.litres)
        self.max_flow_rate_ul_min = float(self.pump.get_flow_rate_max())
        self.max_volume_ml = float(self.pump.get_volume_max())
        self.initialized = True

    def _require_pump(self) -> Any:
        if self.pump is None:
            raise CetoniPumpError("Qmix pump is not initialized.")
        return self.pump

    def _require_position_sensing_initialized_before_enable(self) -> None:
        """Fail closed unless the freshly connected pump reports valid position sensing."""
        pump = self._require_pump()
        with log_call(
            "pump", "check_position_sensing_initialized", response_stage="OBSERVED"
        ) as result:
            initialized = bool(pump.is_position_sensing_initialized())
            result["response"] = initialized
        if not initialized:
            raise CetoniPumpError(
                "Qmix pump position sensing is not initialized after fault recovery; "
                "refusing to enable or command the pump. Resolve the position-sensing "
                "readiness condition in QmixElements before reconnecting."
            )

    def _enable_pump(self) -> None:
        pump = self._require_pump()
        if pump.is_in_fault_state():
            # initialize() clears the device fault before reaching this gate.
            # A remaining or relatched fault must still prevent drive enable.
            detail = ""
            read_last_error = getattr(pump, "read_last_error", None)
            if callable(read_last_error):
                try:
                    last_error = read_last_error()
                    code = getattr(last_error, "code", "unknown")
                    message = getattr(last_error, "message", str(last_error))
                    detail = f" Last device error: code={code}, message={message!r}."
                    if code == 0:
                        detail += (
                            " The SDK's last-error record is resolved, but the pump still reports "
                            "an active fault state; do not treat this as a recovered connection."
                        )
                except Exception as exc:
                    detail = f" Last device error could not be read: {exc}."
            raise CetoniPumpError(
                "Qmix pump remains in a fault state after initialization cleared its fault latch."
                f"{detail} Inspect and resolve the fault in QmixElements before enabling the pump."
            )
        enabled_before = bool(pump.is_enabled())
        if not enabled_before:
            with log_call("pump", "enable", command=True, response_stage="OBSERVED") as result:
                pump.enable(True)
                enabled_after = bool(pump.is_enabled())
                result["response"] = {"enabled_before": False, "enabled_after": enabled_after}
                if not enabled_after:
                    raise CetoniPumpError("Qmix pump enable command returned but enabled readback remained false.")
        else:
            log_transaction(
                "pump",
                "read_enabled",
                response={"enabled": True, "enable_command_sent": False},
                evidence_stage="OBSERVED",
            )

    def _fill_flow_rate(self, requested_ul_min: float | None = None) -> float:
        # Clamped to the currently-configured syringe's own live-reported
        # max_flow_rate_ul_min (2026-08-03): a flat, syringe-independent
        # default was tried first (12000 uL/min = 200 uL/s, verified on
        # real hardware) and caused real "Value range of parameter
        # exceeded" SDK rejections on Refill/Empty
        # (logs/hardware_transactions.log) once a smaller syringe than the
        # one the verification used was actually configured. Clamping
        # means the caller's requested/default target is used whenever the
        # active syringe can reach it, and its magnitude is silently capped
        # (not rejected) otherwise. Preserve the sign because negative flow
        # denotes aspiration while positive flow denotes dispension.
        if self.max_flow_rate_ul_min is None:
            self.max_flow_rate_ul_min = float(self._require_pump().get_flow_rate_max())
        target = requested_ul_min if requested_ul_min is not None else self.default_fill_flow_rate_ul_min
        if target is None:
            return self.max_flow_rate_ul_min
        limit = abs(float(self.max_flow_rate_ul_min))
        return max(-limit, min(float(target), limit))

    def refill(self, flow_rate: float | None = None) -> None:
        pump = self._require_pump()
        with log_call("pump", "refill", command={"requested_flow_rate_ul_min": flow_rate}) as result:
            max_volume = self.max_volume_ml if self.max_volume_ml is not None else float(pump.get_volume_max())
            self.max_volume_ml = max_volume
            effective_flow = self._fill_flow_rate(flow_rate)
            pump.set_fill_level(max_volume, effective_flow)
            result["response"] = max_volume
            result["effective"] = {
                "target_fill_level_ml": max_volume,
                "flow_rate_ul_min": effective_flow,
            }

    def empty(self, flow_rate: float | None = None) -> None:
        with log_call("pump", "empty", command={"requested_flow_rate_ul_min": flow_rate}) as result:
            effective_flow = self._fill_flow_rate(flow_rate)
            self._require_pump().set_fill_level(0.0, effective_flow)
            result["response"] = 0.0
            result["effective"] = {"target_fill_level_ml": 0.0, "flow_rate_ul_min": effective_flow}

    def stop(self) -> None:
        if self.pump is not None:
            with log_call("pump", "stop") as result:
                self.pump.stop_pumping()
                result["response"] = "stopped"

    def generate_flow(self, flow_rate: float) -> None:
        pump = self._require_pump()
        self._enable_pump()
        flow_rate = float(flow_rate)
        if not math.isfinite(flow_rate):
            raise CetoniPumpError(f"Requested flow_rate must be finite, got {flow_rate!r}.")
        if self.max_flow_rate_ul_min is None:
            self.max_flow_rate_ul_min = float(pump.get_flow_rate_max())
        if abs(flow_rate) > abs(self.max_flow_rate_ul_min):
            error = (
                f"Requested flow_rate={flow_rate!r} exceeds the pump's own reported "
                f"max_flow_rate_ul_min={self.max_flow_rate_ul_min!r} -- rejected before reaching the pump SDK."
            )
            log_transaction("pump", "generate_flow", command=flow_rate, success=False, error=error)
            raise CetoniPumpError(error)
        with log_call("pump", "generate_flow", command=flow_rate) as result:
            pump.generate_flow(flow_rate)
            result["response"] = "applied"

    def read_fill_level(self) -> float:
        with log_call("pump", "read_fill_level", response_stage="OBSERVED") as result:
            fill_level = float(self._require_pump().get_fill_level())
            result["response"] = fill_level
        return fill_level

    def set_fill_level(self, fill_level: float, flow_rate: float | None = None) -> None:
        # fill_level is always an absolute mL value -- no auto-detection against
        # a 0.0-1.0 fraction. That heuristic previously misread legitimate small
        # absolute values (e.g. 0.5 mL on a 5 mL syringe) as "50% of capacity".
        pump = self._require_pump()
        self._enable_pump()
        with log_call("pump", "set_fill_level", command=fill_level) as result:
            pump.set_fill_level(
                float(fill_level),
                self._fill_flow_rate(flow_rate),
            )
            result["response"] = "applied"

    def configure_syringe(self, config: dict | None) -> None:
        if not config:
            return
        pump = self._require_pump()
        inner_diameter = (
            config.get("inner_diameter_mm")
            or config.get("diameter_mm")
            or config.get("innerDiameter")
            or config.get("diameter")
        )
        stroke = (
            config.get("max_piston_stroke_mm")
            or config.get("piston_stroke_mm")
            or config.get("stroke_mm")
            or config.get("stroke")
        )
        name = config.get("name")
        if (inner_diameter is None or stroke is None) and name in SYRINGE_PRESETS:
            inner_diameter, stroke = SYRINGE_PRESETS[str(name)]
        if inner_diameter is None or stroke is None:
            raise CetoniPumpError(
                "Qmix syringe configuration requires inner_diameter_mm and "
                "max_piston_stroke_mm, or one of the known presets: "
                + ", ".join(SYRINGE_PRESETS)
            )
        inner_diameter = float(inner_diameter)
        stroke = float(stroke)
        if not (MIN_SYRINGE_INNER_DIAMETER_MM <= inner_diameter <= MAX_SYRINGE_INNER_DIAMETER_MM):
            raise CetoniPumpError(
                f"Syringe inner_diameter_mm={inner_diameter!r} is outside the plausible range "
                f"[{MIN_SYRINGE_INNER_DIAMETER_MM}, {MAX_SYRINGE_INNER_DIAMETER_MM}] mm "
                "(spanning BD's full 1mL-60mL product line) -- rejected before reaching the pump SDK."
            )
        if not (MIN_SYRINGE_STROKE_MM <= stroke <= MAX_SYRINGE_STROKE_MM):
            raise CetoniPumpError(
                f"Syringe max_piston_stroke_mm={stroke!r} is outside the plausible range "
                f"[{MIN_SYRINGE_STROKE_MM}, {MAX_SYRINGE_STROKE_MM}] mm "
                "(this pump module's own absolute mechanical piston travel is up to 65mm, "
                "CETONI Low Pressure Hardware Manual Section 5.1, NEM-B101-02 E) -- "
                "rejected before reaching the pump SDK."
            )
        with log_call("pump", "configure_syringe", command=(inner_diameter, stroke)) as result:
            pump.set_syringe_param(inner_diameter, stroke)
            self.max_flow_rate_ul_min = float(pump.get_flow_rate_max())
            self.max_volume_ml = float(pump.get_volume_max())
            result["response"] = f"max_flow_rate_ul_min={self.max_flow_rate_ul_min}, max_volume_ml={self.max_volume_ml}"

    def configure_flow_unit(self, unit: str | None) -> None:
        pump = self._require_pump()
        text = _normalize_flow_unit(unit)
        prefix = self.qmixpump.UnitPrefix.micro
        time_unit = self.qmixpump.TimeUnit.per_minute
        if text in {"ml/min", "millilitre/min", "milliliter/min"}:
            prefix = self.qmixpump.UnitPrefix.milli
        elif text in {"ul/s", "microlitre/s", "microliter/s"}:
            time_unit = self.qmixpump.TimeUnit.per_second
        elif text in {"ml/s", "millilitre/s", "milliliter/s"}:
            prefix = self.qmixpump.UnitPrefix.milli
            time_unit = self.qmixpump.TimeUnit.per_second
        with log_call("pump", "configure_flow_unit", command=text) as result:
            pump.set_flow_unit(prefix, self.qmixpump.VolumeUnit.litres, time_unit)
            self.max_flow_rate_ul_min = float(pump.get_flow_rate_max())
            result["response"] = f"max_flow_rate_ul_min={self.max_flow_rate_ul_min}"

    def reference_move(self) -> None:
        self.start_reference_move()
        with log_call("pump", "reference_move") as result:
            deadline = time.monotonic() + max(self.reference_move_timeout_s, 0.0)
            while time.monotonic() < deadline:
                if self.reference_move_finished():
                    result["response"] = "calibration finished"
                    return
                time.sleep(0.1)
            raise CetoniPumpError("Qmix pump reference move timed out.")

    def start_reference_move(self) -> None:
        pump = self._require_pump()
        self._enable_pump()
        pump.calibrate()

    def reference_move_finished(self) -> bool:
        return bool(self._require_pump().is_calibration_finished())

    def read_status(self) -> bool:
        if self.pump is None:
            return False
        with log_call("pump", "read_status", response_stage="OBSERVED") as result:
            is_pumping = bool(self.pump.is_pumping())
            result["response"] = is_pumping
        return is_pumping

    def read_flow(self) -> float:
        return float(self._require_pump().get_flow_is())

    def read_fault(self) -> bool:
        return bool(self._require_pump().is_in_fault_state())

    def read_syringe(self) -> tuple[float, float]:
        diameter, stroke = self._require_pump().get_syringe_param()
        return float(diameter), float(stroke)

    def cleanup(self) -> None:
        # Deliberately not wrapped in log_call() -- cleanup() already collects
        # errors from each step (via _run_cleanup_step's own timeout-wrapped
        # thread) and raises once at the end, the standard hardware-cleanup
        # shape; log the
        # overall outcome without altering that collect-then-raise control flow.
        errors: list[str] = []
        if self.bus_started and self.pump is not None:
            errors.extend(self._run_cleanup_step("pump stop", self.stop))
        if self.bus is not None and self.bus_started:
            def stop_bus() -> None:
                with log_call("pump", "bus.stop") as result:
                    self.bus.stop()
                    result["response"] = "stopped"

            errors.extend(self._run_cleanup_step("bus stop", stop_bus))
        if self.bus is not None and self.bus_opened:
            def close_bus() -> None:
                with log_call("pump", "bus.close") as result:
                    self.bus.close()
                    result["response"] = "closed"

            errors.extend(self._run_cleanup_step("bus close", close_bus))
        self.bus = None
        self.pump = None
        self.bus_opened = False
        self.bus_started = False
        self.initialized = False
        if errors:
            log_transaction("pump", "close", success=False, error="; ".join(errors))
            raise CetoniPumpError("; ".join(errors))
        log_transaction("pump", "close", success=True, response="closed")

    def _run_cleanup_step(self, name: str, action) -> list[str]:
        # Cross-module architecture review (2026-08-02): now the shared
        # hw_logging.run_with_timeout() utility -- was previously its own
        # hand-copied implementation of the same shape
        # Application._run_cleanup_call_with_timeout()/
        # PiezoStage._run_disconnect_step() each independently
        # re-implemented. Message wording ("Qmix {name} ...") unchanged.
        error = run_with_timeout(action, f"Qmix {name}", self.cleanup_timeout_s)
        return [error] if error is not None else []
