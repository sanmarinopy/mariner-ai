"""Texto -> voz con OpenAI y reproducción local, publicando el nivel de audio
para que el holograma "module" al hablar."""
from __future__ import annotations

import asyncio
import logging

import numpy as np

from ..config import Settings
from ..core.events import EventBus

log = logging.getLogger("mariner.tts")
TTS_RATE = 24000  # response_format="pcm" -> 24 kHz, 16 bit, mono
LEVEL_MS = 50


class Voice:
    def __init__(self, settings: Settings, bus: EventBus) -> None:
        self.s = settings
        self.bus = bus
        self.client = None
        self.sd = None
        if settings.has_openai:
            from openai import AsyncOpenAI

            self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        try:
            import sounddevice as sd

            sd.query_devices(kind="output")
            self.sd = sd
        except Exception as e:  # sin placa de audio: sólo animación + subtítulos
            log.warning("Sin salida de audio disponible (%s). Se mostrará sólo texto.", e)

    async def speak(self, text: str) -> None:
        pcm = None
        if self.client and self.sd:
            try:
                resp = await self.client.audio.speech.create(
                    model=self.s.tts_model, voice=self.s.tts_voice, input=text,
                    instructions=self.s.tts_style, response_format="pcm",
                )
                data = resp.content if hasattr(resp, "content") else await resp.aread()
                pcm = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
            except Exception:
                log.exception("Falló la síntesis de voz; sigo sólo con texto")
        if pcm is None:
            await self._fake_levels(text)
            return
        await self._play(pcm)

    async def _play(self, pcm: np.ndarray) -> None:
        step = TTS_RATE * LEVEL_MS // 1000
        levels = [float(np.sqrt(np.mean(pcm[i:i + step] ** 2))) for i in range(0, len(pcm), step)]
        peak = max(levels) or 1.0
        loop = asyncio.get_running_loop()
        play = loop.run_in_executor(None, lambda: (self.sd.play(pcm, TTS_RATE), self.sd.wait()))
        for lv in levels:
            await self.bus.publish("audio.level", {"level": min(1.0, lv / peak)})
            await asyncio.sleep(LEVEL_MS / 1000)
        await play
        await self.bus.publish("audio.level", {"level": 0.0})

    async def _fake_levels(self, text: str) -> None:
        # ~65 ms por carácter: aproxima la duración de la frase para animar el holograma
        n = max(10, int(len(text) * 65 / LEVEL_MS))
        t = np.arange(n)
        lv = np.clip(0.45 + 0.35 * np.sin(t * 0.9) * np.sin(t * 0.23) + np.random.rand(n) * 0.2, 0, 1)
        for v in lv:
            await self.bus.publish("audio.level", {"level": float(v)})
            await asyncio.sleep(LEVEL_MS / 1000)
        await self.bus.publish("audio.level", {"level": 0.0})
