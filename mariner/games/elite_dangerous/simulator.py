"""Simulador de vuelo: escribe un Journal + Status.json falsos para probar sin el juego.

Se escribe a disco (no se inyecta en memoria) a propósito: así se prueba exactamente
el mismo camino que con el juego real.
"""
from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timezone
from pathlib import Path

ROUTE = ["Sol", "Alpha Centauri", "Barnard's Star", "Wolf 359", "Lalande 21185", "Sirius"]
STAR = {"Sol": "G", "Alpha Centauri": "G", "Barnard's Star": "M", "Wolf 359": "M",
        "Lalande 21185": "M", "Sirius": "A"}


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Simulator:
    def __init__(self, folder: str | Path, speed: float = 1.0) -> None:
        self.folder = Path(folder)
        self.speed = speed
        self.fuel_cap = 32.0
        self.fuel = 30.0
        self.flags = (1 << 0) | (1 << 3) | (1 << 24)  # atracado, escudos, en nave
        self.journal: Path | None = None
        self.route = list(ROUTE)

    def _write(self, **ev) -> None:
        ev = {"timestamp": _ts(), **ev}
        with self.journal.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev) + "\n")

    def _status(self) -> None:
        low = (1 << 19) if self.fuel / self.fuel_cap < 0.25 else 0
        st = {"timestamp": _ts(), "event": "Status", "Flags": self.flags | low,
              "Pips": [4, 4, 4], "FireGroup": 0, "GuiFocus": 0,
              "Fuel": {"FuelMain": round(self.fuel, 2), "FuelReservoir": 0.5}, "Cargo": 0.0}
        tmp = self.folder / "Status.json.tmp"
        tmp.write_text(json.dumps(st), encoding="utf-8")
        tmp.replace(self.folder / "Status.json")

    async def _wait(self, s: float) -> None:
        await asyncio.sleep(s / self.speed)

    async def run(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%dT%H%M%S")
        self.journal = self.folder / f"Journal.{stamp}.01.log"
        self._write(event="Fileheader", part=1, gameversion="4.0.0.0 (simulado)")
        self._write(event="LoadGame", Commander="Farias", Ship="Krait_MkII", Ship_Localised="Krait Mk II",
                    ShipName="Mariner One", FuelLevel=self.fuel, FuelCapacity=self.fuel_cap)
        self._write(event="Loadout", Ship="krait_mkii", ShipName="Mariner One", HullHealth=1.0,
                    FuelCapacity={"Main": self.fuel_cap, "Reserve": 0.63})
        self._write(event="Location", StarSystem="Sol", Docked=True, StationName="Abraham Lincoln", Body="Earth")
        self._status()
        await self._wait(6)

        while True:
            self._write(event="Undocked", StationName="Estación de origen")
            self.flags &= ~1
            self._status()
            await self._wait(6)
            for i, sysname in enumerate(self.route[1:], start=1):
                self.flags |= 1 << 17  # FSD cargando
                self._status()
                await self._wait(2)
                self._write(event="StartJump", JumpType="Hyperspace", StarSystem=sysname, StarClass=STAR[sysname])
                await self._wait(4)
                used = random.uniform(2.5, 4.5)
                self.fuel = max(1.0, self.fuel - used)
                self.flags &= ~(1 << 17)
                self._write(event="FSDJump", StarSystem=sysname, JumpDist=round(random.uniform(4, 9), 2),
                            FuelUsed=round(used, 2), FuelLevel=round(self.fuel, 2))
                self._status()
                await self._wait(7)
                if sysname == "Wolf 359":
                    self._write(event="Interdicted", Submitted=False, Interdictor="Pirata desconocido", IsPlayer=False)
                    await self._wait(3)
                    self._write(event="UnderAttack", Target="You")
                    self._write(event="ShieldState", ShieldsUp=False)
                    self.flags &= ~(1 << 3)
                    self._status()
                    await self._wait(2)
                    self._write(event="HullDamage", Health=0.71, PlayerPilot=True, Fighter=False)
                    await self._wait(5)
                    self._write(event="ShieldState", ShieldsUp=True)
                    self.flags |= 1 << 3
                    self._status()
                    await self._wait(6)
            self._write(event="DockingGranted", LandingPad=7, StationName="Sirius Atmospherics")
            await self._wait(6)
            self._write(event="Docked", StationName="Sirius Atmospherics", StarSystem="Sirius")
            self.flags |= 1
            await self._wait(3)
            self._write(event="RefuelAll", Amount=round(self.fuel_cap - self.fuel, 2))
            self._write(event="RepairAll", Cost=1200)
            self.fuel = self.fuel_cap
            self._status()
            await self._wait(20)
            self.route.reverse()
