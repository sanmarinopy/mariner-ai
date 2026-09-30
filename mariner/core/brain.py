"""Cerebro: conversación con OpenAI + function calling hacia el Game Pack."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable

from ..config import Settings
from ..games.base import GamePack

log = logging.getLogger("mariner.brain")
MAX_TOOL_ROUNDS = 4
Emit = Callable[[str], Awaitable[None]]

ASSISTANT_MODE = """MODO ASISTENTE: en este momento no hay telemetría del juego conectada ({game} no está
abierto o no envía datos). Actúa como asistente personal general: responde cualquier consulta
(conocimiento general, explicaciones, ideas, cálculos, organización) y también dudas sobre {game},
pero sin datos en vivo. Si te piden el estado de la nave u otros datos del juego en vivo, aclara
brevemente que la telemetría no está conectada."""


class Brain:
    def __init__(self, settings: Settings, pack: GamePack, usage=None) -> None:
        self.settings = settings
        self.pack = pack
        self.usage = usage
        self.history: list[dict[str, Any]] = []
        self.client = None
        self._effort = settings.reasoning_effort or None  # se ajusta si el modelo lo rechaza
        if settings.has_openai:
            from openai import AsyncOpenAI

            self.client = AsyncOpenAI(api_key=settings.openai_api_key)

    def system_prompt(self, speaker: str | None = None) -> str:
        s = self.settings
        rules = (f"Responde en idioma '{s.language}', en {s.max_sentences} frases como máximo. "
                 "Nada de listas ni markdown: todo se convierte en voz.")
        # Orden pensado para el caché de OpenAI: lo fijo primero, lo que cambia (estado) al final.
        if self.pack.telemetry_active():
            parts = [s.personality.format(name=s.assistant_name).strip(), self.pack.persona(), rules,
                     self.pack.context()]
        else:
            parts = [s.personality.format(name=s.assistant_name).strip(), ASSISTANT_MODE.format(game=self.pack.name),
                     rules]
        if speaker:
            parts.append(f"Quien te habla ahora fue identificado por voz como: {speaker}.")
        return "\n\n".join(p for p in parts if p)

    async def _create(self, kwargs: dict[str, Any]):
        """Llama al modelo; si rechaza reasoning_effort, prueba "none" y después sin el parámetro."""
        from openai import BadRequestError

        while True:
            if self._effort:
                kwargs["reasoning_effort"] = self._effort
            else:
                kwargs.pop("reasoning_effort", None)
            try:
                return await self.client.chat.completions.create(**kwargs)
            except BadRequestError as e:
                if "reasoning_effort" not in str(e) or not self._effort:
                    raise
                nuevo = "none" if self._effort != "none" else None
                log.warning("El modelo %s no acepta reasoning_effort=%s; uso %s",
                            kwargs["model"], self._effort, nuevo or "(sin parámetro)")
                self._effort = nuevo

    async def _round(self, kwargs: dict[str, Any], emit: Emit | None):
        """Una vuelta con el modelo. Devuelve (texto, resto_sin_emitir, tool_calls).

        Con emit: usa streaming y entrega cada frase apenas está completa, para que la voz
        empiece a hablar mientras el modelo sigue escribiendo."""
        model = kwargs["model"]
        if emit is None:
            resp = await self._create(kwargs)
            if self.usage:
                await self.usage.chat(model, getattr(resp, "usage", None))
            msg = resp.choices[0].message
            calls = [{"id": c.id, "name": c.function.name, "arguments": c.function.arguments or ""}
                     for c in (msg.tool_calls or [])]
            return msg.content or "", "", calls

        stream = await self._create(dict(kwargs, stream=True, stream_options={"include_usage": True}))
        content, buf, usage = "", "", None
        calls: dict[int, dict[str, str]] = {}
        async for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            if not chunk.choices:
                continue
            d = chunk.choices[0].delta
            if getattr(d, "content", None):
                content += d.content
                buf += d.content
                buf = await _emit_sentences(buf, emit)
            for tc in getattr(d, "tool_calls", None) or []:
                c = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    c["id"] = tc.id
                if tc.function is not None:
                    c["name"] += tc.function.name or ""
                    c["arguments"] += tc.function.arguments or ""
        if self.usage:
            await self.usage.chat(model, usage)
        return content, buf, [calls[k] for k in sorted(calls)]

    async def ask(self, text: str, speaker: str | None = None, emit: Emit | None = None) -> str:
        """Responde a `text`. Si se pasa `emit`, cada frase se entrega apenas está lista."""
        if not self.client:
            reply = self.pack.offline_reply(text)
            if emit:
                await emit(reply)
            return reply

        s = self.settings
        keep = max(0, s.history_turns) * 2
        user = {"role": "user", "content": text}
        messages: list[dict[str, Any]] = [{"role": "system", "content": self.system_prompt(speaker)}]
        messages += (self.history[-keep:] if keep else []) + [user]
        tools = (self.pack.tools() if self.pack.telemetry_active() else None) or None

        reply = ""
        for _ in range(MAX_TOOL_ROUNDS):
            kwargs: dict[str, Any] = {"model": s.chat_model, "messages": messages,
                                      "max_completion_tokens": s.max_reply_tokens}
            if tools:
                kwargs["tools"] = tools
            content, rest, calls = await self._round(kwargs, emit)
            if not calls:
                reply = content.strip()
                if emit and rest.strip():
                    await emit(rest.strip())
                break
            messages.append({
                "role": "assistant", "content": content or "",
                "tool_calls": [{"id": c["id"], "type": "function",
                                "function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls],
            })
            for c in calls:
                try:
                    args = json.loads(c["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                log.info("tool -> %s(%s)", c["name"], args)
                result = await self.pack.call_tool(c["name"], args)
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
        if not reply:
            reply = "Perdí el hilo de los sistemas, comandante. ¿Puede repetir?"
            if emit:
                await emit(reply)

        self.history += [user, {"role": "assistant", "content": reply}]
        self.history = self.history[-keep:] if keep else []
        return reply


_SENTENCE_END = re.compile(r"[.!?…](?=\s)")
MIN_SENTENCE = 18  # no cortar en frases muy cortas ("Sí.") para que la voz suene continua


async def _emit_sentences(buf: str, emit: Emit) -> str:
    while True:
        m = _SENTENCE_END.search(buf, MIN_SENTENCE)
        if not m:
            return buf
        sentence, buf = buf[: m.end()].strip(), buf[m.end():].lstrip()
        if sentence:
            await emit(sentence)
