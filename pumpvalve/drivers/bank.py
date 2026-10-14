"""One Qmix bus session exposing the pump units in its Elements project."""

from __future__ import annotations

from pathlib import Path

from .cetoni import CetoniPump
from .simulated import SimulatedPump


class CetoniPumpBank:
    """Address up to four Qmix pumps configured in one Elements project.

    Qmix owns one bus session per Elements project.  This wrapper keeps that
    session in one ``CetoniPump`` instance and temporarily selects the target
    SDK pump for each serialized HAL operation.
    """

    MAX_UNITS = 4

    def __init__(self) -> None:
        self._session = CetoniPump()
        self._pumps: list[object] = []
        self._limits: list[tuple[float, float]] = []

    def set_configuration_path(self, path: Path) -> None:
        self._session.configuration_path = path

    @property
    def unit_count(self) -> int:
        return len(self._pumps)

    def initialize(self) -> None:
        self._session.initialize()
        count = int(self._session.qmixpump.Pump.get_no_of_pumps())
        if not 1 <= count <= self.MAX_UNITS:
            self._session.cleanup()
            raise RuntimeError(f"Qmix Elements configuration declares {count} pumps; supported range is 1..{self.MAX_UNITS}")
        self._pumps = []
        self._limits = []
        for index in range(count):
            pump = self._session.pump if index == 0 else self._session.qmixpump.Pump()
            if index:
                pump.lookup_by_device_index(index)
                pump.set_flow_unit(self._session.qmixpump.UnitPrefix.micro, self._session.qmixpump.VolumeUnit.litres, self._session.qmixpump.TimeUnit.per_minute)
                pump.set_volume_unit(self._session.qmixpump.UnitPrefix.milli, self._session.qmixpump.VolumeUnit.litres)
            self._pumps.append(pump)
            self._limits.append((float(pump.get_volume_max()), float(pump.get_flow_rate_max())))

    def cleanup(self) -> None:
        for index in range(self.unit_count):
            try:
                self.stop(index)
            except Exception:
                pass
        self._session.cleanup()
        self._pumps = []
        self._limits = []

    def _call(self, index: int, name: str, *args):
        if not 0 <= index < self.unit_count:
            raise ValueError(f"Pump unit must be within 1..{self.unit_count}")
        previous_pump, previous_volume, previous_flow = self._session.pump, self._session.max_volume_ml, self._session.max_flow_rate_ul_min
        volume, flow = self._limits[index]
        self._session.pump, self._session.max_volume_ml, self._session.max_flow_rate_ul_min = self._pumps[index], volume, flow
        try:
            value = getattr(self._session, name)(*args)
            self._limits[index] = (float(self._session.max_volume_ml), float(self._session.max_flow_rate_ul_min))
            return value
        finally:
            self._session.pump, self._session.max_volume_ml, self._session.max_flow_rate_ul_min = previous_pump, previous_volume, previous_flow

    def capabilities(self, index: int) -> tuple[float, float]:
        self._call(index, "read_status")
        return self._limits[index]

    def generate_flow(self, index: int, flow: float) -> None: self._call(index, "generate_flow", flow)
    def stop(self, index: int) -> None: self._call(index, "stop")
    def refill(self, index: int, flow: float | None = None) -> None: self._call(index, "refill", flow)
    def empty(self, index: int, flow: float | None = None) -> None: self._call(index, "empty", flow)
    def set_fill_level(self, index: int, level: float, flow: float | None = None) -> None: self._call(index, "set_fill_level", level, flow)
    def read_fill_level(self, index: int) -> float: return self._call(index, "read_fill_level")
    def read_status(self, index: int) -> bool: return self._call(index, "read_status")
    def read_flow(self, index: int) -> float: return self._call(index, "read_flow")
    def read_fault(self, index: int) -> bool: return self._call(index, "read_fault")
    def read_syringe(self, index: int) -> tuple[float, float]: return self._call(index, "read_syringe")
    def configure_syringe(self, index: int, config: dict | None) -> None: self._call(index, "configure_syringe", config)
    def configure_flow_unit(self, index: int, unit: str) -> None: self._call(index, "configure_flow_unit", unit)
    def start_reference_move(self, index: int) -> None: self._call(index, "start_reference_move")
    def reference_move_finished(self, index: int) -> bool: return self._call(index, "reference_move_finished")

    def read_valve_position(self, index: int) -> int | None:
        pump = self._pumps[index]
        if not pump.has_valve():
            return None
        valve = pump.get_valve()
        if int(valve.number_of_valve_positions()) != 2:
            raise RuntimeError(f"Pump {index + 1} valve is not a two-position valve")
        return int(valve.actual_valve_position()) + 1

    def set_valve_position(self, index: int, position: int) -> int:
        if position not in (1, 2):
            raise ValueError("Built-in valve position must be 1 or 2")
        if self.read_status(index):
            raise RuntimeError("Stop pump flow before switching its built-in valve")
        current = self.read_valve_position(index)
        if current is None:
            raise RuntimeError(f"Pump {index + 1} has no built-in valve")
        self._pumps[index].get_valve().switch_valve_to_position(position - 1)
        observed = self.read_valve_position(index)
        if observed != position:
            raise RuntimeError(f"Pump {index + 1} valve reports position {observed}, expected {position}")
        return observed


class SimulatedPumpBank:
    """Offline bank matching the Qmix-discovered bank contract."""

    def __init__(self, unit_count: int = 2) -> None:
        if not 1 <= unit_count <= 4:
            raise ValueError("unit_count must be within 1..4")
        self._pumps = [SimulatedPump() for _ in range(unit_count)]
        self.configuration_path: Path | None = None
        self._valve_positions = [1] * unit_count

    def set_configuration_path(self, path: Path) -> None:
        self.configuration_path = path

    @property
    def unit_count(self) -> int: return len(self._pumps)
    def initialize(self) -> None:
        for pump in self._pumps: pump.initialize()
    def cleanup(self) -> None:
        for pump in self._pumps: pump.cleanup()
    def _pump(self, index: int) -> SimulatedPump: return self._pumps[index]
    def capabilities(self, index: int) -> tuple[float, float]:
        pump = self._pump(index); return pump.max_volume_ml, pump.max_flow_rate_ul_min
    def generate_flow(self, index: int, flow: float) -> None: self._pump(index).generate_flow(flow)
    def stop(self, index: int) -> None: self._pump(index).stop()
    def refill(self, index: int, flow: float | None = None) -> None: self._pump(index).refill(flow)
    def empty(self, index: int, flow: float | None = None) -> None: self._pump(index).empty(flow)
    def set_fill_level(self, index: int, level: float, flow: float | None = None) -> None: self._pump(index).set_fill_level(level, flow)
    def read_fill_level(self, index: int) -> float: return self._pump(index).read_fill_level()
    def read_status(self, index: int) -> bool: return self._pump(index).read_status()
    def read_flow(self, index: int) -> float: return self._pump(index).read_flow()
    def read_fault(self, index: int) -> bool: return self._pump(index).read_fault()
    def read_syringe(self, index: int) -> tuple[float, float] | None: return self._pump(index).read_syringe()
    def configure_syringe(self, index: int, config: dict | None) -> None: self._pump(index).configure_syringe(config)
    def configure_flow_unit(self, index: int, unit: str) -> None: self._pump(index).configure_flow_unit(unit)
    def start_reference_move(self, index: int) -> None: self._pump(index).start_reference_move()
    def reference_move_finished(self, index: int) -> bool: return self._pump(index).reference_move_finished()
    def read_valve_position(self, index: int) -> int | None: return self._valve_positions[index]
    def set_valve_position(self, index: int, position: int) -> int:
        if position not in (1, 2):
            raise ValueError("Built-in valve position must be 1 or 2")
        if self.read_status(index):
            raise RuntimeError("Stop pump flow before switching its built-in valve")
        self._valve_positions[index] = position
        return position
