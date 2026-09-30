"""Contrato de un "Game Pack".

Cada juego (Elite Dangerous, Star Citizen, MSFS, etc.) es un paquete que aporta:
  - persona(): personalidad / system prompt del asistente para ese juego
  - tools(): funciones que el modelo puede invocar (function calling de OpenAI)
  - call_tool(): ejecución de esas funciones
  - context(): foto breve del estado del juego que se inyecta en cada pregunta
  - hud(): datos para dibujar en el holograma
  - offline_reply(): respuestas sin IA (demo sin API key / sin internet)
  - start()/stop(): arrancar lectores de telemetría

El núcleo NO conoce nada del juego: sólo habla con esta interfaz.
Para un juego nuevo se crea mariner/games/<id>/ con una clase que herede de GamePack.
"""
from __future__ import annotations

import abc
from typing import Any, Awaitable, Callable

from ..config import Settings
from ..core.events import EventBus

Callout = Callable[[str, int], Awaitable[None]]  # (texto, prioridad)


class GamePack(abc.ABC):
    id: str = "base"
    name: str = "Base"

    def __init__(self, bus: EventBus, settings: Settings, callout: Callout) -> None:
        self.bus = bus
        self.settings = settings
        # callout(texto, prioridad): aviso proactivo que el asistente dirá en voz alta
        self.callout = callout

    @abc.abstractmethod
    def persona(self) -> str: ...

    def tools(self) -> list[dict[str, Any]]:
        return []

    async def call_tool(self, name: str, args: dict[str, Any]) -> str:
        return f"Herramienta desconocida: {name}"

    def context(self) -> str:
        return ""

    def hud(self) -> dict[str, Any]:
        return {}

    def offline_reply(self, text: str) -> str:
        return "Sin enlace con el núcleo de IA. Configura OPENAI_API_KEY para conversar."

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


def load_pack(pack_id: str, bus: EventBus, settings: Settings, callout: Callout) -> GamePack:
    import importlib

    mod = importlib.import_module(f"mariner.games.{pack_id}")
    return mod.Pack(bus, settings, callout)
