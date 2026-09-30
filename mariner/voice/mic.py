"""Captura de micrófono con detección de voz (VAD) por energía.

Simple y sin dependencias pesadas: suficiente para la demo. En la Raspberry conviene
un array de micrófonos con cancelación de eco (p.ej. ReSpeaker) y, más adelante,
una palabra de activación local (openWakeWord) para no enviar todo a la nube.
"""
from __future__ import annotations

import asyncio
import io
import logging
import wave
from collections import deque

import numpy as np

log = logging.getLogger("mariner.mic")

RATE = 16000
FRAME_MS = 30
FRAME = RATE * FRAME_MS // 1000


class Microphone:
    def __init__(self, device: str | int | None = None, threshold: float = 0.015,
                 silence_ms: int = 900, min_ms: int = 400, max_s: float = 15.0) -> None:
        import sounddevice as sd  # import diferido: sólo si se usa voz

        self.sd = sd
        self.device = int(device) if isinstance(device, str) and device.isdigit() else (device or None)
        self.threshold = threshold
        self.silence_frames = silence_ms // FRAME_MS
        self.min_frames = min_ms // FRAME_MS
        self.max_frames = int(max_s * 1000 / FRAME_MS)
        self.muted = False  # se activa mientras el asistente habla (evita escucharse a sí mismo)
        self._q: asyncio.Queue[np.ndarray] = asyncio.Queue()
        self._stream = None

    def start(self) -> None:
        loop = asyncio.get_running_loop()

        def cb(indata, frames, t, status):  # hilo de audio
            loop.call_soon_threadsafe(self._q.put_nowait, indata[:, 0].copy())

        self._stream = self.sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                                           blocksize=FRAME, device=self.device, callback=cb)
        self._stream.start()
        log.info("Micrófono activo (%s)", self.sd.query_devices(self.device, "input")["name"])

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()

    async def utterances(self, on_start=None):
        """Generador asíncrono: entrega cada frase hablada como WAV (bytes)."""
        pre = deque(maxlen=10)  # ~300 ms antes de detectar voz
        speech: list[np.ndarray] = []
        silent = 0
        active = False
        while True:
            frame = await self._q.get()
            if self.muted:
                pre.clear(); speech.clear(); active = False; silent = 0
                continue
            rms = float(np.sqrt(np.mean(frame ** 2)))
            loud = rms > self.threshold
            if not active:
                pre.append(frame)
                if loud:
                    active, speech, silent = True, list(pre), 0
                    if on_start:
                        await on_start()
                continue
            speech.append(frame)
            silent = 0 if loud else silent + 1
            if silent >= self.silence_frames or len(speech) >= self.max_frames:
                active = False
                if len(speech) - silent >= self.min_frames:
                    yield to_wav(np.concatenate(speech))
                speech, silent = [], 0
                pre.clear()


def to_wav(samples: np.ndarray, rate: int = RATE) -> bytes:
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()
