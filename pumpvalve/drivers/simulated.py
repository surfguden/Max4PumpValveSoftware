from __future__ import annotations

import math


class SimulatedPump:
    def __init__(self) -> None:
        self.initialized = False
        self.flow_ul_min = 0.0
        self.fill_level_ml = 0.0
        self.max_volume_ml = 1.0
        self.max_flow_rate_ul_min = 10000.0
        self.syringe_config: dict | None = None
        self.flow_unit: str | None = None
        self.referenced = False

    def initialize(self) -> None:
        self.initialized = True

    def cleanup(self) -> None:
        self.stop()
        self.initialized = False

    def clear_fault_and_reinitialize(self) -> None:
        self.initialize()

    def generate_flow(self, flow_rate: float) -> None:
        flow_rate = float(flow_rate)
        if not math.isfinite(flow_rate) or abs(flow_rate) > self.max_flow_rate_ul_min:
            raise ValueError(
                f"flow_rate must be finite and within +/-{self.max_flow_rate_ul_min} ul/min"
            )
        self.flow_ul_min = flow_rate

    def stop(self) -> None:
        self.flow_ul_min = 0.0

    def refill(self, flow_rate: float | None = None) -> None:
        self.fill_level_ml = self.max_volume_ml
        self.flow_ul_min = 0.0

    def empty(self, flow_rate: float | None = None) -> None:
        self.fill_level_ml = 0.0
        self.flow_ul_min = 0.0

    def set_fill_level(self, fill_level: float, flow_rate: float | None = None) -> None:
        self.fill_level_ml = fill_level
        self.flow_ul_min = 0.0

    def read_fill_level(self) -> float:
        return self.fill_level_ml

    def configure_syringe(self, config: dict | None) -> None:
        self.syringe_config = config
        if not config:
            return
        preset_volumes = {"BD 1ml": 1.0, "BD 5ml": 5.0, "BD 10ml": 10.0}
        if config.get("name") in preset_volumes:
            self.max_volume_ml = preset_volumes[config["name"]]
        elif (
            config.get("inner_diameter_mm") is not None
            and config.get("max_piston_stroke_mm") is not None
        ):
            radius_mm = float(config["inner_diameter_mm"]) / 2.0
            self.max_volume_ml = (
                math.pi * radius_mm**2 * float(config["max_piston_stroke_mm"]) / 1000.0
            )
        elif config.get("volume_ml") is not None:
            self.max_volume_ml = float(config["volume_ml"])

    def configure_flow_unit(self, unit: str | None) -> None:
        self.flow_unit = unit

    def reference_move(self) -> None:
        self.referenced = True

    def start_reference_move(self) -> None:
        self.reference_move()

    def reference_move_finished(self) -> bool:
        return self.referenced

    def read_status(self) -> bool:
        return self.flow_ul_min != 0.0

    def read_flow(self) -> float:
        return self.flow_ul_min

    def read_fault(self) -> bool:
        return False

    def read_syringe(self) -> tuple[float, float] | None:
        if not self.syringe_config:
            return None
        diameter = self.syringe_config.get("inner_diameter_mm")
        stroke = self.syringe_config.get("max_piston_stroke_mm")
        if diameter is None or stroke is None:
            return None
        return float(diameter), float(stroke)
