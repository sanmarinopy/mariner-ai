"""Reproductor continuo: recibe audio por partes (streaming) y lo reproduce sin cortes."""
from __future__ import annotations

import collections
import threading

import numpy as np

RATE = 24000


class StreamPlayer:
    def __init__(self, sd, rate: int = RATE) -> None:
        self._q: collections.deque[np.ndarray] = collections.deque()
        self._lock = threading.Lock()
        self._cur: np.ndarray | None = None
        self._pos = 0
        self.level = 0.0  # RMS de lo que está sonando (para animar el holograma)
        self._stream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32",
                                       blocksize=480, callback=self._callback)

    def start(self) -> None:
        self._stream.start()

    def stop(self) -> None:
        self._stream.stop()
        self._stream.close()

    def push(self, pcm: np.ndarray) -> None:
        if len(pcm):
            with self._lock:
                self._q.append(np.asarray(pcm, dtype=np.float32))

    def clear(self) -> None:
        with self._lock:
            self._q.clear()
            self._cur = None

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._cur is not None or bool(self._q)

    def _callback(self, out, frames, _time, _status) -> None:
        buf = out[:, 0]
        filled = 0
        with self._lock:
            while filled < frames:
                if self._cur is None:
                    if not self._q:
                        break
                    self._cur, self._pos = self._q.popleft(), 0
                n = min(frames - filled, len(self._cur) - self._pos)
                buf[filled:filled + n] = self._cur[self._pos:self._pos + n]
                self._pos += n
                filled += n
                if self._pos >= len(self._cur):
                    self._cur = None
        buf[filled:] = 0
        self.level = float(np.sqrt(np.mean(buf[:filled] ** 2))) if filled else 0.0
