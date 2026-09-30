"""Bus de eventos asíncrono muy simple (pub/sub por tópico).

Tópicos usados:
  assistant.state   {"state": idle|listening|thinking|speaking}
  assistant.say     {"text": str, "kind": "reply"|"callout"}
  audio.level       {"level": 0..1}
  user.said         {"text": str, "speaker": str|None}
  game.hud          {...}  estado resumido para la interfaz
  bridge.message    {"kind": "journal"|"status", "data": {...}}
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

Handler = Callable[[str, Any], Awaitable[None]]
log = logging.getLogger("mariner.bus")


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> None:
        """topic='*' recibe todo."""
        self._subs[topic].append(handler)

    async def publish(self, topic: str, data: Any = None) -> None:
        for h in list(self._subs.get(topic, [])) + list(self._subs.get("*", [])):
            try:
                await h(topic, data)
            except Exception:  # un suscriptor roto no debe tumbar al resto
                log.exception("Error en suscriptor de %s", topic)
