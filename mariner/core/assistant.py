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
        # audio ya generado esperando su turno (se genera la frase siguiente mientras suena la actual)
        self._ready: asyncio.Queue = asyncio.Queue(maxsize=2)
        self._seq = itertools.count()
        self._t_heard: float | None = None  # cuándo terminaste de hablar (para medir la demora)
        self._t_stt = 0.0
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
            self._t_heard = None
            await self.set_state("idle")
            return
        if self.s.only_known_speakers and self.stt and speaker not in self.stt.known:
            log.info("Ignorado: hablante no registrado (%s)", speaker)
            self._t_heard = None
            await self.set_state("idle")
            return
        async with self._busy:
            await self.set_state("thinking")
            if self._t_heard is None:
                self._t_heard = time.monotonic()
            t_ai = time.monotonic()
            first = True

            async def emit(sentence: str) -> None:
                nonlocal first
                if first:
                    log.info("IA: primera frase en %.2f s", time.monotonic() - t_ai)
                    first = False
                await self._speech.put((-2, next(self._seq), sentence, "reply", time.monotonic()))

            try:
                await self.brain.ask(text, speaker, emit=emit)
            except Exception as e:
                log.exception("Error consultando a la IA")
                await emit(f"Falla de enlace con el núcleo: {type(e).__name__}.")

    async def _synth_worker(self) -> None:
        """Etapa 1: toma frases de la cola y genera su audio (mientras suena la anterior)."""
        while True:
            _, _, text, kind, born = await self._speech.get()
            if kind == "callout" and time.monotonic() - born > STALE_CALLOUT_S:
                continue  # aviso viejo: la situación ya cambió, no tiene sentido decirlo
            pcm = await self.voice.prepare(text)
            await self._ready.put((text, kind, pcm))

    async def _play_worker(self) -> None:
        """Etapa 2: reproduce en orden, silenciando el micrófono mientras habla."""
        while True:
            text, kind, pcm = await self._ready.get()
            if kind == "reply" and self._t_heard is not None:
                log.info("Demora total: %.2f s desde que terminaste de hablar (transcripción %.2f s)",
                         time.monotonic() - self._t_heard, self._t_stt)
                self._t_heard, self._t_stt = None, 0.0
            log.info("%s: %s", "Aviso" if kind == "callout" else "Responde", text)
            await self.bus.publish("assistant.say", {"text": text, "kind": kind})
            await self.set_state("speaking")
            if self.mic:
                self.mic.muted = True
            try:
                await self.voice.play(pcm, text)
            finally:
                if self.mic and self._ready.empty() and self._speech.empty() and not self._busy.locked():
                    await asyncio.sleep(0.2)  # cola de eco de los parlantes
                    self.mic.muted = False
                if self._speech.empty() and self._ready.empty() and not self._busy.locked():
                    await self.set_state("idle")

    async def _listen_loop(self) -> None:
        async def on_start():
            if self._state == "idle":
                await self.set_state("listening")

        async for wav in self.mic.utterances(on_start=on_start):
            # el micrófono entrega la frase tras VAD_SILENCE_MS de silencio: esa espera también cuenta
            self._t_heard = time.monotonic() - self.s.vad_silence_ms / 1000
            t0 = time.monotonic()
            try:
                text, speaker = await self.stt.transcribe(wav)
                self._t_stt = time.monotonic() - t0
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

        from .usage import UsageMeter

        self.usage = UsageMeter(self.s, self.bus)
        self.pack = load_pack(self.s.game_pack, self.bus, self.s, self.callout)
        self.brain = Brain(self.s, self.pack, self.usage)
        self.voice = Voice(self.s, self.bus, self.usage)
        await self.pack.start()
        tasks = [asyncio.create_task(self._synth_worker(), name="synth"),
                 asyncio.create_task(self._play_worker(), name="play")]

        if voice_input:
            if not self.s.has_openai:
                log.warning("--voice requiere OPENAI_API_KEY (la transcripción es en la nube). Sigo sin micrófono.")
            else:
                from ..voice.mic import Microphone
                from ..voice.stt import Transcriber

                self.mic = Microphone(self.s.mic_device or None, self.s.vad_threshold, self.s.vad_silence_ms)
                self.stt = Transcriber(self.s, self.usage)
                self.mic.start()
                tasks.append(asyncio.create_task(self._listen_loop(), name="listen"))

        log.info("Unidad %s · perfil %s · modelo %s · voz %s · efecto %s", self.s.device_id, self.s.profile,
                 self.s.chat_model, self.s.tts_voice, self.s.tts_effect)
        if self.s.env_overrides:
            log.warning("El .env pisa al perfil en: %s  (vacíalos en .env para usar el perfil)",
                        ", ".join(self.s.env_overrides))
        mode = "IA en línea" if self.s.has_openai else "modo sin conexión"
        await self.callout(f"{self.s.assistant_name} en línea. Sistemas de {self.pack.name} conectados, {mode}.", 1)
        try:
            await asyncio.gather(*tasks)
        finally:
            await self.pack.stop()
            if self.mic:
                self.mic.stop()
