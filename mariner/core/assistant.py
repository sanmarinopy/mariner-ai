"""Orquestador: une oído (mic+STT), cerebro, voz y avisos del juego en una sola cola de habla."""
from __future__ import annotations

import asyncio
import itertools
import logging
import re
import time
import unicodedata

from ..config import Settings
from ..games.base import GamePack
from .brain import Brain
from .events import EventBus

log = logging.getLogger("mariner.assistant")
STALE_CALLOUT_S = 12


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


class Assistant:
    def __init__(self, settings: Settings, bus: EventBus) -> None:
        self.s = settings
        self.bus = bus
        self.pack: GamePack | None = None
        self.brain: Brain | None = None
        self.voice = None
        self.mic = None
        self.stt = None
        # cola de habla con prioridad: (−prioridad, orden, texto, tipo, creado)
        self._speech: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._seq = itertools.count()
        self._state = "idle"
        self._busy = asyncio.Lock()

    async def set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            await self.bus.publish("assistant.state", {"state": state})

    async def callout(self, text: str, prio: int = 1) -> None:
        """Aviso proactivo del juego (no pasa por la IA: latencia mínima)."""
        await self._speech.put((-prio, next(self._seq), text, "callout", time.monotonic()))

    async def handle_user_text(self, text: str, speaker: str | None = None) -> None:
        text = text.strip()
        if not text:
            return
        await self.bus.publish("user.said", {"text": text, "speaker": speaker})
        if self.s.require_wake_word and _norm(self.s.assistant_name) not in _norm(text):
            await self.set_state("idle")
            return
        if self.s.only_known_speakers and self.stt and speaker not in self.stt.known:
            log.info("Ignorado: hablante no registrado (%s)", speaker)
            await self.set_state("idle")
            return
        async with self._busy:
            await self.set_state("thinking")
            try:
                reply = await self.brain.ask(text, speaker)
            except Exception as e:
                log.exception("Error consultando a la IA")
                reply = f"Falla de enlace con el núcleo: {type(e).__name__}."
            await self._speech.put((-2, next(self._seq), reply, "reply", time.monotonic()))

    async def _speech_worker(self) -> None:
        while True:
            _, _, text, kind, born = await self._speech.get()
            if kind == "callout" and time.monotonic() - born > STALE_CALLOUT_S:
                continue  # aviso viejo: la situación ya cambió, no tiene sentido decirlo
            log.info("%s: %s", "Aviso" if kind == "callout" else "Responde", text)
            await self.bus.publish("assistant.say", {"text": text, "kind": kind})
            await self.set_state("speaking")
            if self.mic:
                self.mic.muted = True
            try:
                await self.voice.speak(text)
            finally:
                if self.mic:
                    await asyncio.sleep(0.25)  # cola de eco de los parlantes
                    self.mic.muted = False
                if self._speech.empty():
                    await self.set_state("idle")

    async def _listen_loop(self) -> None:
        async def on_start():
            if self._state == "idle":
                await self.set_state("listening")

        async for wav in self.mic.utterances(on_start=on_start):
            try:
                text, speaker = await self.stt.transcribe(wav)
            except Exception:
                log.exception("Error transcribiendo")
                await self.set_state("idle")
                continue
            log.info("Oído%s: %s", f" ({speaker})" if speaker else "", text)
            if not text or re.fullmatch(r"[\W_]*", text):
                await self.set_state("idle")
                continue
            asyncio.create_task(self.handle_user_text(text, speaker))

    async def run(self, voice_input: bool) -> None:
        from ..games.base import load_pack
        from ..voice.tts import Voice

        self.pack = load_pack(self.s.game_pack, self.bus, self.s, self.callout)
        self.brain = Brain(self.s, self.pack)
        self.voice = Voice(self.s, self.bus)
        await self.pack.start()
        tasks = [asyncio.create_task(self._speech_worker(), name="speech")]

        if voice_input:
            if not self.s.has_openai:
                log.warning("--voice requiere OPENAI_API_KEY (la transcripción es en la nube). Sigo sin micrófono.")
            else:
                from ..voice.mic import Microphone
                from ..voice.stt import Transcriber

                self.mic = Microphone(self.s.mic_device or None, self.s.vad_threshold, self.s.vad_silence_ms)
                self.stt = Transcriber(self.s)
                self.mic.start()
                tasks.append(asyncio.create_task(self._listen_loop(), name="listen"))

        mode = "IA en línea" if self.s.has_openai else "modo sin conexión"
        await self.callout(f"{self.s.assistant_name} en línea. Sistemas de {self.pack.name} conectados, {mode}.", 1)
        try:
            await asyncio.gather(*tasks)
        finally:
            await self.pack.stop()
            if self.mic:
                self.mic.stop()
