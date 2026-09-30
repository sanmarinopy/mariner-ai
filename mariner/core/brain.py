"""Cerebro: conversación con OpenAI + function calling hacia el Game Pack."""
from __future__ import annotations

import json
import logging
from typing import Any

from ..config import Settings
from ..games.base import GamePack

log = logging.getLogger("mariner.brain")
MAX_HISTORY = 16  # mensajes (usuario+asistente) que se conservan
MAX_TOOL_ROUNDS = 4


class Brain:
    def __init__(self, settings: Settings, pack: GamePack) -> None:
        self.settings = settings
        self.pack = pack
        self.history: list[dict[str, Any]] = []
        self.client = None
        if settings.has_openai:
            from openai import AsyncOpenAI

            self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    def _system(self, speaker: str | None) -> str:
        parts = [self.pack.persona(), self.pack.context()]
        if speaker:
            parts.append(f"Quien te habla ahora fue identificado por voz como: {speaker}.")
        return "\n\n".join(p for p in parts if p)

    async def ask(self, text: str, speaker: str | None = None) -> str:
        if not self.client:
            return self.pack.offline_reply(text)

        user = {"role": "user", "content": text}
        messages: list[dict[str, Any]] = [{"role": "system", "content": self._system(speaker)}]
        messages += self.history[-MAX_HISTORY:] + [user]
        tools = self.pack.tools() or None

        reply = ""
        for _ in range(MAX_TOOL_ROUNDS):
            kwargs: dict[str, Any] = {"model": self.settings.chat_model, "messages": messages}
            if tools:
                kwargs["tools"] = tools
            resp = await self.client.chat.completions.create(**kwargs)
            msg = resp.choices[0].message
            if not msg.tool_calls:
                reply = (msg.content or "").strip()
                break
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {"id": c.id, "type": "function",
                         "function": {"name": c.function.name, "arguments": c.function.arguments}}
                        for c in msg.tool_calls
                    ],
                }
            )
            for call in msg.tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                log.info("tool -> %s(%s)", call.function.name, args)
                result = await self.pack.call_tool(call.function.name, args)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
        if not reply:
            reply = "Perdí el hilo de los sistemas, comandante. ¿Puede repetir?"

        self.history += [user, {"role": "assistant", "content": reply}]
        self.history = self.history[-MAX_HISTORY:]
        return reply
