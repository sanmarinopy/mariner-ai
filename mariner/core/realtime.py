"""Motor de voz en tiempo real (OpenAI Realtime API).

Una sola conexión WebSocket permanente:
  micrófono ──(audio en vivo)──▶ OpenAI ──(voz en streaming)──▶ efecto ──▶ parlante

OpenAI detecta el fin de tu frase, entiende el audio directamente y empieza a responder con voz
mientras todavía genera el resto. Sin transcribir primero ni esperar la respuesta completa.
Justo antes de cada respuesta se envía el estado actualizado de la nave.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import TYPE_CHECKING, Any

import numpy as np

from ..voice.effects import StreamFX

if TYPE_CHECKING:
    from .assistant import Assistant

log = logging.getLogger("mariner.realtime")
RATE = 24000
REALTIME_VOICES = {"alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"}


class RealtimeEngine:
    def __init__(self, assistant: "Assistant") -> None:
        self.a = assistant
        self.s = assistant.s
        self.conn = None
        self.player = None
        self.mic = None
        self.fx = StreamFX(self.s.tts_effect, self.s.tts_effect_mix)
        self._t_stop: float | None = None
        self._first_audio = True
        self._pending_tool = False
        self._transcript = ""
        self._ready = asyncio.Event()
        self._replying = False  # hay una respuesta a una pregunta en curso
        self._callout_ids: set[str] = set()
        self._callout_done: asyncio.Event | None = None

    # ------------------------------------------------------------------ configuración
    def _voice(self) -> str:
        v = self.s.tts_voice
        if v not in REALTIME_VOICES:
            log.warning("La voz '%s' no existe en tiempo real; uso 'marin'. Opciones: %s",
                        v, ", ".join(sorted(REALTIME_VOICES)))
            return "marin"
        return v

    def _voice_rules(self) -> str:
        """Lo primero que lee el modelo: idioma y acento (los modelos de voz priorizan el inicio)."""
        accent = self.s.accent or "español latinoamericano neutro"
        return (f"IDIOMA Y PRONUNCIACIÓN: habla siempre en {accent}, con pronunciación de hablante nativo. "
                "Nada de acento extranjero ni entonación de alguien que aprende el idioma. "
                f"Estilo de voz: {self.s.tts_style}")

    def _instructions(self) -> str:
        return (self._voice_rules() + "\n\n" + self.a.brain.system_prompt()
                + "\nFrases cortas y claras. No ofrezcas ayuda adicional al final de cada respuesta.")

    def _tools(self) -> list[dict[str, Any]]:
        out = []
        for t in self.a.pack.tools():
            f = t.get("function", t)
            out.append({"type": "function", "name": f["name"], "description": f.get("description", ""),
                        "parameters": f.get("parameters", {"type": "object", "properties": {}})})
        return out

    def _session(self) -> dict[str, Any]:
        session: dict[str, Any] = {
            "type": "realtime",
            "output_modalities": ["audio"],
            "instructions": self._instructions(),
            "max_output_tokens": self.s.max_reply_tokens,
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": RATE},
                    "noise_reduction": {"type": "near_field"},
                    "transcription": {"model": self.s.stt_model, "language": self.s.language},
                    "turn_detection": {
                        "type": "server_vad",
                        "silence_duration_ms": self.s.vad_silence_ms,
                        "prefix_padding_ms": 300,
                        "create_response": False,  # la pedimos nosotros, con el estado de la nave al día
                        "interrupt_response": False,
                    },
                },
                "output": {"format": {"type": "audio/pcm", "rate": RATE}, "voice": self._voice()},
            },
        }
        tools = self._tools()
        if tools:
            session["tools"] = tools
        if self.s.realtime_reasoning:
            session["reasoning"] = {"effort": self.s.realtime_reasoning}
        return session

    # ------------------------------------------------------------------ ciclo principal
    async def run(self) -> None:
        import sounddevice as sd
        from openai import AsyncOpenAI

        from ..voice.mic import Microphone
        from ..voice.player import StreamPlayer

        self.player = StreamPlayer(sd)
        self.player.start()
        self.mic = Microphone(self.s.mic_device or None, rate=RATE)
        self.a.mic = self.mic  # para que los avisos también silencien el micrófono
        self.mic.start()
        client = AsyncOpenAI(api_key=self.s.openai_api_key)
        log.info("Modo tiempo real: %s · voz %s · efecto %s", self.s.realtime_model, self._voice(), self.s.tts_effect)
        try:
            async with client.realtime.connect(model=self.s.realtime_model) as conn:
                self.conn = conn
                await conn.session.update(session=self._session())
                self._ready.set()
                await asyncio.gather(self._send_audio(), self._events(), self._levels())
        finally:
            self.conn = None
            self.mic.stop()
            self.player.stop()
            self.player = None

    async def _send_audio(self) -> None:
        async for frame in self.mic.frames():
            pcm16 = (np.clip(frame, -1, 1) * 32767).astype("<i2").tobytes()
            await self.conn.input_audio_buffer.append(audio=base64.b64encode(pcm16).decode())

    async def _levels(self) -> None:
        was = False
        while True:
            busy = self.player.busy
            if busy or was:
                await self.a.bus.publish("audio.level", {"level": min(1.0, self.player.level * 5)})
            was = busy
            await asyncio.sleep(0.05)

    async def _respond(self) -> None:
        self._replying = True
        self._first_audio = True
        self._transcript = ""
        # instrucciones frescas: el estado de la nave cambia todo el tiempo
        await self.conn.response.create(response={"instructions": self._instructions()})

    async def _events(self) -> None:
        async for ev in self.conn:
            t = ev.type
            if t == "input_audio_buffer.speech_started":
                await self.a.set_state("listening")
            elif t == "input_audio_buffer.speech_stopped":
                self._t_stop = time.monotonic() - self.s.vad_silence_ms / 1000
                await self.a.set_state("thinking")
            elif t == "input_audio_buffer.committed":
                await self._respond()
            elif t == "response.created":
                md = getattr(ev.response, "metadata", None) or {}
                if md.get("kind") == "callout":
                    self._callout_ids.add(ev.response.id)
            elif t == "conversation.item.input_audio_transcription.completed":
                text = (ev.transcript or "").strip()
                if text:
                    log.info("Oído: %s", text)
                    await self.a.bus.publish("user.said", {"text": text, "speaker": None})
            elif t == "response.output_audio.delta":
                pcm = np.frombuffer(base64.b64decode(ev.delta), dtype="<i2").astype(np.float32) / 32768.0
                if self._first_audio:
                    self._first_audio = False
                    if self._t_stop is not None:
                        log.info("Demora total: %.2f s desde que terminaste de hablar", time.monotonic() - self._t_stop)
                        self._t_stop = None
                    self.mic.muted = True
                    await self.a.set_state("speaking")
                self.player.push(self.fx.process(pcm))
            elif t == "response.output_audio_transcript.delta":
                self._transcript += ev.delta or ""
            elif t == "response.output_audio_transcript.done":
                text = (ev.transcript or self._transcript).strip()
                if getattr(ev, "response_id", None) in self._callout_ids:
                    continue  # aviso: el texto ya se mostró al encolarlo
                if text:
                    log.info("Responde: %s", text)
                    await self.a.bus.publish("assistant.say", {"text": text, "kind": "reply"})
            elif t == "response.function_call_arguments.done":
                try:
                    args = json.loads(ev.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                log.info("tool -> %s(%s)", ev.name, args)
                result = await self.a.pack.call_tool(ev.name, args)
                await self.conn.conversation.item.create(
                    item={"type": "function_call_output", "call_id": ev.call_id, "output": result})
                self._pending_tool = True
            elif t == "response.done":
                usage = getattr(ev.response, "usage", None)
                if self.a.usage and usage:
                    await self.a.usage.realtime(self.s.realtime_model, usage)
                rid = getattr(ev.response, "id", None)
                if rid in self._callout_ids:
                    self._callout_ids.discard(rid)
                    if self._callout_done:
                        self._callout_done.set()
                    continue
                if self._pending_tool:
                    self._pending_tool = False
                    await self._respond()
                else:
                    self._replying = False
                    asyncio.create_task(self._finish())
            elif t == "error":
                err = getattr(ev, "error", None)
                log.error("OpenAI tiempo real: %s", getattr(err, "message", err))

    async def _finish(self) -> None:
        """Cuando termina de sonar: reabre el micrófono y vuelve a reposo."""
        while self.player.busy:
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.25)  # cola de eco de los parlantes
        self.mic.muted = False
        await self.a.set_state("idle")

    # ------------------------------------------------------------------ usado por el orquestador
    async def send_text(self, text: str) -> None:
        """Mensaje escrito desde la interfaz."""
        await self._ready.wait()
        if self.conn is None:
            return
        self._t_stop = time.monotonic()
        await self.a.set_state("thinking")
        await self.conn.conversation.item.create(
            item={"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]})
        await self._respond()

    async def play(self, pcm: np.ndarray | None, text: str) -> None:
        """Dice un aviso del juego con la MISMA voz del modo tiempo real.

        Se pide como respuesta "fuera de la conversación": no entra en el historial y no se mezcla
        con lo que estés hablando. Espera a que termine la respuesta en curso, si la hay."""
        await self._ready.wait()
        if self.conn is None or self.player is None:
            return
        while self._replying:
            await asyncio.sleep(0.05)
        self._callout_done = asyncio.Event()
        self._first_audio = True
        await self.conn.response.create(response={
            "conversation": "none",
            "input": [],
            "metadata": {"kind": "callout"},
            "instructions": (self._voice_rules() + "\n\nLee en voz alta exactamente este texto, palabra "
                             "por palabra, sin agregar ni quitar nada, con tono de aviso de cabina.\n"
                             f"Texto: {text}"),
        })
        try:
            await asyncio.wait_for(self._callout_done.wait(), timeout=20)
        except asyncio.TimeoutError:
            log.warning("El aviso no terminó a tiempo: %s", text)
        await self._finish()
