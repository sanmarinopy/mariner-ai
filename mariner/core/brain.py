"""Cerebro: conversación con OpenAI + function calling hacia el Game Pack."""
from __future__ import annotations

import json
import logging
from typing import Any

from ..config import Settings
from ..games.base import GamePack

log = logging.getLogger("mariner.brain")
MAX_TOOL_ROUNDS = 4


class Brain:
    def __init__(self, settings: Settings, pack: GamePack, usage=None) -> None:
        self.settings = settings
        self.pack = pack
        self.usage = usage
        self.history: list[dict[str, Any]] = []
        self.client = None
        if settings.has_openai:
            from openai import AsyncOpenAI

            self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    def system_prompt(self, speaker: str | None = None) -> str:
        s = self.settings
        rules = (f"Responde en idioma '{s.language}', en {s.max_sentences} frases como máximo. "
                 "Nada de listas ni markdown: todo se convierte en voz.")
        # Orden pensado para el caché de OpenAI: lo fijo primero, lo que cambia (estado) al final.
        parts = [s.personality.format(name=s.assistant_name).strip(), self.pack.persona(), rules,
                 self.pack.context()]
        if speaker:
            parts.append(f"Quien te habla ahora fue identificado por voz como: {speaker}.")
        return "\n\n".join(p for p in parts if p)

    async def ask(self, text: str, speaker: str | None = None) -> str:
        if not self.client:
            return self.pack.offline_reply(text)

        s = self.settings
        keep = max(0, s.history_turns) * 2
        user = {"role": "user", "content": text}
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt(speaker)}]
        messages += (self.history[-keep:] if keep else []) + [user]
        tools = self.pack.tools() or None

        reply = ""
        for _ in range(MAX_TOOL_ROUNDS):
            kwargs: dict[str, Any] = {"model": s.chat_model, "messages": messages,
                                      "max_completion_tokens": s.max_reply_tokens}
            if s.reasoning_effort:
                kwargs["reasoning_effort"] = s.reasoning_effort
            if tools:
                kwargs["tools"] = tools
            resp = await self.client.chat.completions.create(**kwargs)
            if self.usage:
                await self.usage.chat(s.chat_model, getattr(resp, "usage", None))
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
        self.history = self.history[-keep:] if keep else []
        return reply
