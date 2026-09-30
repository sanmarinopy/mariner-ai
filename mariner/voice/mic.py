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
                 silence_ms: int = 900, min_ms: int = 400, max_s: float = 15.0, rate: int = RATE) -> None:
        import sounddevice as sd  # import diferido: sólo si se usa voz

        self.sd = sd
        self.rate = rate
        self.frame = rate * FRAME_MS // 1000
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
        try:
            self._open(loop, self.rate)
        except Exception as e:
            # algunos micrófonos no aceptan la frecuencia pedida: se captura a la nativa y se convierte
            native = int(self.sd.query_devices(self.device, "input")["default_samplerate"])
            log.info("El micrófono no acepta %d Hz (%s); capturo a %d Hz y convierto", self.rate, e, native)
            self._open(loop, native)
        self._stream.start()
        log.info("Micrófono activo (%s)", self.sd.query_devices(self.device, "input")["name"])

    def _open(self, loop, capture_rate: int) -> None:
        target = self.rate

        def cb(indata, frames, t, status):  # hilo de audio
            x = indata[:, 0].copy()
            if capture_rate != target:
                n = int(round(len(x) * target / capture_rate))
                x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
            loop.call_soon_threadsafe(self._q.put_nowait, x)

        self._stream = self.sd.InputStream(samplerate=capture_rate, channels=1, dtype="float32",
                                           blocksize=capture_rate * FRAME_MS // 1000,
                                           device=self.device, callback=cb)

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    async def frames(self):
        """Entrega el audio crudo en bloques de 30 ms (modo tiempo real). Nada mientras está silenciado."""
        while True:
            frame = await self._q.get()
            if not self.muted:
                yield frame

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
