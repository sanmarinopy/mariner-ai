"""Modelo del estado de la nave a partir de eventos del Journal y Status.json."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

# Bits de Status.json -> Flags (documentación oficial del Journal de Frontier)
FLAGS = {
    0: "docked", 1: "landed", 2: "landing_gear_down", 3: "shields_up", 4: "supercruise",
    5: "flight_assist_off", 6: "hardpoints_deployed", 7: "in_wing", 8: "lights_on",
    9: "cargo_scoop_deployed", 10: "silent_running", 11: "scooping_fuel",
    16: "fsd_mass_locked", 17: "fsd_charging", 18: "fsd_cooldown", 19: "low_fuel",
    20: "overheating", 22: "in_danger", 23: "being_interdicted", 24: "in_main_ship",
    25: "in_fighter", 26: "in_srv", 27: "analysis_mode", 28: "night_vision", 30: "fsd_jump",
}


def decode_flags(flags: int) -> dict[str, bool]:
    return {name: bool(flags & (1 << bit)) for bit, name in FLAGS.items()}


@dataclass
class ShipState:
    commander: str = ""
    ship: str = ""
    ship_name: str = ""
    system: str = ""
    body: str = ""
    station: str = ""
    docked: bool = False
    fuel_main: float | None = None
    fuel_capacity: float | None = None
    hull: float | None = None  # 0..1
    shields_up: bool | None = None
    flags: dict[str, bool] = field(default_factory=dict)
    pips: list[int] | None = None  # [sys, eng, wep] en medios pips
    cargo: float | None = None
    last_jump_ly: float | None = None
    route_remaining: int | None = None
    recent: deque = field(default_factory=lambda: deque(maxlen=30))

    @property
    def fuel_pct(self) -> float | None:
        if self.fuel_main is None or not self.fuel_capacity:
            return None
        return max(0.0, min(1.0, self.fuel_main / self.fuel_capacity))

    def apply_event(self, ev: dict[str, Any]) -> None:
        e = ev.get("event")
        g = ev.get
        if e in ("LoadGame", "Commander"):
            self.commander = g("Commander", g("Name", self.commander)) or self.commander
            if e == "LoadGame":
                self.ship = g("Ship_Localised") or g("Ship", self.ship)
                self.ship_name = g("ShipName", self.ship_name)
                self.fuel_main = g("FuelLevel", self.fuel_main)
                self.fuel_capacity = g("FuelCapacity", self.fuel_capacity)
        elif e == "Location":
            self.system = g("StarSystem", self.system)
            self.body = g("Body", "")
            self.docked = bool(g("Docked", False))
            self.station = g("StationName", "") if self.docked else ""
        elif e == "FSDJump":
            self.system = g("StarSystem", self.system)
            self.body, self.station, self.docked = "", "", False
            self.last_jump_ly = g("JumpDist")
            if g("FuelLevel") is not None:
                self.fuel_main = g("FuelLevel")
        elif e == "Docked":
            self.docked, self.station = True, g("StationName", "")
            self.system = g("StarSystem", self.system)
        elif e == "Undocked":
            self.docked, self.station = False, ""
        elif e == "ApproachBody":
            self.body = g("Body", "")
        elif e == "LeaveBody":
            self.body = ""
        elif e == "HullDamage":
            if g("PlayerPilot", True):
                self.hull = g("Health", self.hull)
        elif e == "RepairAll" or (e == "Repair" and g("Item") in ("Hull", "Wear")):
            self.hull = 1.0
        elif e == "ShieldState":
            self.shields_up = bool(g("ShieldsUp"))
        elif e == "FuelScoop":
            self.fuel_main = g("Total", self.fuel_main)
        elif e in ("RefuelAll", "RefuelPartial") and self.fuel_capacity:
            self.fuel_main = min(self.fuel_capacity, (self.fuel_main or 0) + (g("Amount") or 0))
        elif e == "Loadout":
            self.ship = g("Ship", self.ship)
            self.ship_name = g("ShipName", self.ship_name)
            fc = g("FuelCapacity") or {}
            if isinstance(fc, dict) and fc.get("Main"):
                self.fuel_capacity = fc["Main"]
            if g("HullHealth") is not None:
                self.hull = g("HullHealth")
        elif e == "NavRoute" or e == "FSDTarget":
            if g("RemainingJumpsInRoute") is not None:
                self.route_remaining = g("RemainingJumpsInRoute")
        elif e == "NavRouteClear":
            self.route_remaining = None

        if e not in ("Music", "ReceiveText", "Fileheader"):
            self.recent.append({k: v for k, v in ev.items() if not k.startswith("_")})

    def apply_status(self, st: dict[str, Any]) -> None:
        if "Flags" in st:
            self.flags = decode_flags(int(st["Flags"]))
            self.docked = self.flags.get("docked", self.docked)
            self.shields_up = self.flags.get("shields_up", self.shields_up)
        fuel = st.get("Fuel") or {}
        if "FuelMain" in fuel:
            self.fuel_main = fuel["FuelMain"]
        if "Pips" in st:
            self.pips = st["Pips"]
        if "Cargo" in st:
            self.cargo = st["Cargo"]

    def summary(self) -> dict[str, Any]:
        active = [k for k, v in self.flags.items() if v]
        return {
            "comandante": self.commander or None,
            "nave": self.ship_name or self.ship or None,
            "modelo_nave": self.ship or None,
            "sistema": self.system or None,
            "cuerpo": self.body or None,
            "estacion": self.station or None,
            "atracado": self.docked,
            "combustible_t": round(self.fuel_main, 2) if self.fuel_main is not None else None,
            "combustible_pct": round(self.fuel_pct * 100) if self.fuel_pct is not None else None,
            "casco_pct": round(self.hull * 100) if self.hull is not None else None,
            "escudos_activos": self.shields_up,
            "pips_sys_eng_wep": [p / 2 for p in self.pips] if self.pips else None,
            "carga_t": self.cargo,
            "ultimo_salto_ly": self.last_jump_ly,
            "saltos_restantes_ruta": self.route_remaining,
            "flags_activos": active,
        }
