"""Game Pack: Elite Dangerous (Horizons / Odyssey)."""
from __future__ import annotations

import json
import time
from typing import Any

from ..base import GamePack
from .journal import JournalWatcher
from .state import ShipState

SCOOPABLE = set("KGBFOAM")  # clases estelares de las que se puede recoger combustible

# Reglas propias del juego. La personalidad viene del perfil (profiles/*.toml).
PERSONA = """Contexto: el comandante juega Elite Dangerous y tú eres la IA de su nave.
- Usa el ESTADO DE LA NAVE adjunto; si falta un dato, dilo sin inventar.
- Para mecánicas, módulos, ingeniería o sistemas responde con lo que sabes de Elite Dangerous
  y aclara si algo puede haber cambiado con actualizaciones.
- Si pide una acción física en la nave (tren de aterrizaje, salto, etc.) explica que aún
  no tienes control de mandos en esta versión.
"""


class Pack(GamePack):
    id = "elite_dangerous"
    name = "Elite Dangerous"

    def __init__(self, bus, settings, callout) -> None:
        super().__init__(bus, settings, callout)
        self.state = ShipState()
        self.watcher: JournalWatcher | None = None
        self._last_callout: dict[str, float] = {}
        self._hull_marks_said: set[int] = set()
        self._prev_flags: dict[str, bool] = {}
        self._last_hud = 0.0

    # ------------------------------------------------------------------ ciclo de vida
    async def start(self) -> None:
        if self.settings.game_source == "bridge":
            self.bus.subscribe("bridge.message", self._on_bridge)
        else:
            self.watcher = JournalWatcher(self.settings.elite_journal_dir, self.on_event, self.on_status)
            self.watcher.start()

    async def stop(self) -> None:
        if self.watcher:
            await self.watcher.stop()

    async def _on_bridge(self, _topic: str, msg: dict[str, Any]) -> None:
        if msg.get("kind") == "journal":
            await self.on_event(msg["data"])
        elif msg.get("kind") == "status":
            await self.on_status(msg["data"])

    # ------------------------------------------------------------------ telemetría
    async def on_event(self, ev: dict[str, Any]) -> None:
        self.state.apply_event(ev)
        if not ev.get("_replay"):
            await self._callouts_for_event(ev)
        await self._push_hud(force=True)

    async def on_status(self, st: dict[str, Any]) -> None:
        self.state.apply_status(st)
        await self._callouts_for_flags()
        await self._push_hud()

    async def _push_hud(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_hud < 0.5:
            return
        self._last_hud = now
        await self.bus.publish("game.hud", self.hud())

    # ------------------------------------------------------------------ avisos proactivos
    async def _say(self, key: str, text: str, prio: int = 1, cooldown: float = 8.0) -> None:
        cfg = self.settings
        if not cfg.callouts_enabled or key.rstrip("0123456789") in cfg.callouts_muted:
            return
        now = time.monotonic()
        if now - self._last_callout.get(key, -1e9) < cooldown:
            return
        self._last_callout[key] = now
        await self.callout(text, prio)

    async def _callouts_for_event(self, ev: dict[str, Any]) -> None:
        e, g = ev.get("event"), ev.get
        if e == "StartJump" and g("JumpType") == "Hyperspace":
            star = g("StarClass", "")
            extra = "" if not star else (
                " Estrella recargable." if star[:1] in SCOOPABLE else " Ojo: estrella no recargable."
            )
            await self._say("startjump", f"Salto iniciado hacia {g('StarSystem', 'destino desconocido')}.{extra}")
        elif e == "FSDJump":
            dist = g("JumpDist")
            d = f" Distancia {dist:.1f} años luz." if isinstance(dist, (int, float)) else ""
            await self._say("fsdjump", f"Llegada a {g('StarSystem')}.{d}", cooldown=2)
        elif e == "DockingGranted":
            await self._say("dockgrant", f"Atraque autorizado. Plataforma {g('LandingPad')}.")
        elif e == "DockingDenied":
            await self._say("dockdeny", f"Atraque denegado. Motivo: {g('Reason', 'no informado')}.", 2)
        elif e == "Docked":
            await self._say("docked", f"Atraque completado en {g('StationName')}. Buen trabajo, comandante.")
        elif e == "Undocked":
            await self._say("undocked", "Despegue confirmado. Controles en sus manos, comandante.")
        elif e == "Interdicted" or e == "Interdiction":
            await self._say("interdict", "¡Alerta! Interdicción en curso.", 3, cooldown=20)
        elif e == "UnderAttack":
            await self._say("attack", "Estamos bajo fuego.", 3, cooldown=15)
        elif e == "ShieldState":
            if g("ShieldsUp"):
                await self._say("shields_up", "Escudos restablecidos.", 2)
            else:
                await self._say("shields_down", "¡Escudos caídos!", 3, cooldown=5)
        elif e == "HullDamage" and g("PlayerPilot", True):
            pct = int((g("Health") or 0) * 100)
            for mark in (75, 50, 25, 10):
                if pct <= mark and mark not in self._hull_marks_said:
                    self._hull_marks_said.add(mark)
                    await self._say(f"hull{mark}", f"Integridad del casco al {pct} por ciento.", 3, 0)
                    break
        elif e in ("RepairAll", "Repair"):
            self._hull_marks_said.clear()
        elif e == "ApproachBody":
            await self._say("approach", f"Aproximación a {g('Body')}.")
        elif e == "Touchdown" and g("PlayerControlled", True):
            await self._say("touchdown", "Contacto con la superficie.")

    async def _callouts_for_flags(self) -> None:
        f, p = self.state.flags, self._prev_flags
        rising = lambda k: f.get(k) and not p.get(k)  # noqa: E731
        if rising("low_fuel"):
            await self._say("lowfuel", "Advertencia: combustible por debajo del 25 por ciento.", 2, 60)
        if rising("overheating"):
            await self._say("heat", "¡Temperatura crítica!", 3, 10)
        if rising("being_interdicted"):
            await self._say("interdict", "¡Alerta! Intento de interdicción.", 3, 20)
        if rising("fsd_mass_locked") and f.get("supercruise") is False:
            pass  # demasiado frecuente para anunciarlo
        self._prev_flags = dict(f)

    # ------------------------------------------------------------------ IA
    def persona(self) -> str:
        return PERSONA

    def context(self) -> str:
        return "ESTADO DE LA NAVE (tiempo real): " + json.dumps(self.state.summary(), ensure_ascii=False)

    def tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_ship_status",
                    "description": "Devuelve el estado actual completo de la nave del comandante.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_recent_events",
                    "description": "Últimos eventos del Journal del juego (saltos, atraques, combate, escaneos).",
                    "parameters": {
                        "type": "object",
                        "properties": {"count": {"type": "integer", "minimum": 1, "maximum": 30}},
                    },
                },
            },
        ]

    async def call_tool(self, name: str, args: dict[str, Any]) -> str:
        if name == "get_ship_status":
            return json.dumps(self.state.summary(), ensure_ascii=False)
        if name == "get_recent_events":
            n = int(args.get("count", 10))
            return json.dumps(list(self.state.recent)[-n:], ensure_ascii=False)
        return await super().call_tool(name, args)

    def hud(self) -> dict[str, Any]:
        s = self.state
        return {
            "game": self.name,
            "title": s.system or "Sin telemetría",
            "subtitle": s.station or s.body or (s.ship_name or s.ship or ""),
            "gauges": [
                {"id": "fuel", "label": "COMB", "value": s.fuel_pct},
                {"id": "hull", "label": "CASCO", "value": s.hull},
            ],
            "alerts": [k for k in ("low_fuel", "overheating", "in_danger", "being_interdicted") if s.flags.get(k)],
            "shields": s.shields_up,
        }

    def offline_reply(self, text: str) -> str:
        t = text.lower()
        s = self.state.summary()
        parts: list[str] = []
        if any(w in t for w in ("hola", "buenas", "saludos")):
            parts.append(f"Hola, comandante {s['comandante'] or ''}.".replace(" .", "."))
        if any(w in t for w in ("estado", "reporte", "informe")):
            t += " combustible casco sistema"
        if any(w in t for w in ("dónde", "donde", "sistema", "ubicación", "ubicacion")):
            if not s["sistema"]:
                parts.append("Sin datos de navegación todavía.")
            else:
                lugar = f", atracados en {s['estacion']}" if s["estacion"] else ""
                parts.append(f"Estamos en {s['sistema']}{lugar}.")
        if any(w in t for w in ("combustible", "fuel", "tanque")):
            parts.append("Sin lectura de combustible." if s["combustible_pct"] is None else
                         f"Combustible al {s['combustible_pct']} por ciento, {s['combustible_t']} toneladas.")
        if "casco" in t:
            parts.append("Casco sin datos." if s["casco_pct"] is None else f"Casco al {s['casco_pct']} por ciento.")
        if parts:
            return " ".join(parts)
        return "Modo sin conexión: puedo informar combustible, casco, ubicación o estado general."
