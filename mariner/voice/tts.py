"""Texto -> voz con OpenAI y reproducción local, publicando el nivel de audio
para que el holograma "module" al hablar."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path

import numpy as np

from ..config import Settings
from ..core.events import EventBus
from . import effects

log = logging.getLogger("mariner.tts")
TTS_RATE = 24000  # response_format="pcm" -> 24 kHz, 16 bit, mono
LEVEL_MS = 50


class Voice:
    def __init__(self, settings: Settings, bus: EventBus, usage=None) -> None:
        self.s = settings
        self.bus = bus
        self.usage = usage
        self.cache_dir = Path(settings.data_dir) / "tts_cache"
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

    def _cache_path(self, text: str) -> Path:
        key = "|".join((self.s.tts_model, self.s.tts_voice, self._instructions(), text))
        return self.cache_dir / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".pcm")

    def _instructions(self) -> str:
        accent = f"Habla en {self.s.accent}, con pronunciación nativa. " if self.s.accent else ""
        return accent + self.s.tts_style

    async def synthesize(self, text: str) -> tuple[bytes, bool]:
        """Devuelve (pcm 24 kHz s16le, vino_de_caché). Registra el consumo."""
        path = self._cache_path(text)
        if self.s.tts_cache and path.exists():
            data, cached = path.read_bytes(), True
        else:
            resp = await self.client.audio.speech.create(
                model=self.s.tts_model, voice=self.s.tts_voice, input=text,
                instructions=self._instructions(), response_format="pcm",
            )
            data = resp.content if hasattr(resp, "content") else await resp.aread()
            cached = False
            if self.s.tts_cache and len(text) <= 200:  # sólo frases cortas: avisos y respuestas repetidas
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        if self.usage:
            await self.usage.tts(self.s.tts_model, len(data) / 2 / TTS_RATE, len(text), cached)
        return data, cached

    async def prepare(self, text: str) -> np.ndarray | None:
        """Genera el audio listo para reproducir (con efecto). None = sin audio (sólo texto)."""
        if not (self.client and self.sd):
            return None
        try:
            data, _ = await self.synthesize(text)
            pcm = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
            # el efecto se aplica acá: la caché guarda la voz limpia
            return effects.apply(pcm, self.s.tts_effect, self.s.tts_effect_mix)
        except Exception:
            log.exception("Falló la síntesis de voz; sigo sólo con texto")
            return None

    async def play(self, pcm: np.ndarray | None, text: str) -> None:
        if pcm is None:
            await self._fake_levels(text)
        else:
            await self._play(pcm)

    async def speak(self, text: str) -> None:
        await self.play(await self.prepare(text), text)

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
