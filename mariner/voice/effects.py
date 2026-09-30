"""Efectos de voz locales (sin costo: se aplican en el equipo, después de generar el audio).

Presets:
  none      voz tal cual
  cabina    sonido de altavoz de cabina, muy leve y limpio (no altera la pronunciación)
  nave      intercomunicador de cabina: eco metálico corto y banda de radio amplia
  androide  IA de nave: modulación sutil + resonancia metálica
  robot     computadora clásica: modulación fuerte, eco corto y algo de "bits"

StreamFX procesa audio por partes conservando el estado entre partes, así el efecto
suena igual con audio en streaming (modo tiempo real) que con una frase completa.
"""
from __future__ import annotations

import math

import numpy as np

RATE = 24000

try:  # scipy acelera el filtro; sin scipy se usa una versión en Python (más lenta, mismo resultado)
    from scipy.signal import lfilter as _lfilter
except Exception:  # pragma: no cover
    _lfilter = None

PRESETS: dict[str, list[tuple]] = {
    "none": [],
    "cabina": [("comb", 9, 0.18), ("band", 110, 8500)],
    "nave": [("comb", 7, 0.30), ("band", 140, 7000)],
    "androide": [("ring", 38, 0.30), ("comb", 4.5, 0.40), ("band", 200, 6000)],
    "robot": [("ring", 85, 0.65), ("comb", 3, 0.50), ("crush", 10, 2), ("band", 220, 4500)],
}


def _biquad(kind: str, f: float, q: float = 0.707) -> tuple[np.ndarray, np.ndarray]:
    w0 = 2 * math.pi * f / RATE
    alpha, c = math.sin(w0) / (2 * q), math.cos(w0)
    if kind == "lp":
        b = [(1 - c) / 2, 1 - c, (1 - c) / 2]
    else:
        b = [(1 + c) / 2, -(1 + c), (1 + c) / 2]
    a = [1 + alpha, -2 * c, 1 - alpha]
    return np.array(b) / a[0], np.array(a) / a[0]


def _filt(b: np.ndarray, a: np.ndarray, x: np.ndarray, zi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if _lfilter is not None:
        y, zf = _lfilter(b, a, x, zi=zi)
        return y.astype(np.float32), zf
    # Forma directa transpuesta II (equivalente a lfilter)
    y = np.empty_like(x)
    z0, z1 = float(zi[0]), float(zi[1])
    b0, b1, b2 = b
    _, a1, a2 = a
    for i, xi in enumerate(x):
        yi = b0 * xi + z0
        z0 = b1 * xi - a1 * yi + z1
        z1 = b2 * xi - a2 * yi
        y[i] = yi
    return y, np.array([z0, z1])


class StreamFX:
    def __init__(self, preset: str = "none", mix: float = 1.0) -> None:
        if preset not in PRESETS:
            raise ValueError(f"Efecto desconocido: {preset}. Opciones: {', '.join(PRESETS)}")
        self.chain = PRESETS[preset]
        self.mix = min(1.0, max(0.0, mix))
        self.n = 0  # muestras procesadas (fase continua de la modulación)
        self.state: list = []
        for op, *p in self.chain:
            if op == "comb":
                ms, g = p
                d = max(1, int(RATE * ms / 1000))
                self.state.append({"d": d, "g": g, "hist": np.zeros(d * 5, np.float32),
                                   "norm": 1 / (1 + sum(g ** k for k in range(1, 6)))})
            elif op == "band":
                lo, hi = p
                hp, lp = _biquad("hp", lo), _biquad("lp", hi)
                self.state.append({"hp": hp, "lp": lp, "zh": np.zeros(2), "zl": np.zeros(2)})
            else:
                self.state.append(None)

    @property
    def active(self) -> bool:
        return bool(self.chain) and self.mix > 0

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float32)
        if not self.active or len(x) == 0:
            return x
        y = x.copy()
        for (op, *p), st in zip(self.chain, self.state):
            if op == "ring":
                hz, m = p
                t = (self.n + np.arange(len(y))) / RATE
                y = y * (1 - m) + y * np.sin(2 * np.pi * hz * t).astype(np.float32) * m
            elif op == "comb":
                h = st["hist"]
                ext = np.concatenate([h, y])
                out = y.copy()
                for k in range(1, 6):
                    s = len(h) - st["d"] * k
                    out += (st["g"] ** k) * ext[s: s + len(y)]
                st["hist"] = ext[-len(h):]
                y = out * st["norm"] * 1.6
            elif op == "band":
                y, st["zh"] = _filt(*st["hp"], y, st["zh"])
                y, st["zl"] = _filt(*st["lp"], y, st["zl"])
            elif op == "crush":
                bits, hold = p
                q = 2 ** (bits - 1)
                y = np.round(y * q) / q
                if hold > 1:
                    idx = (np.arange(len(y)) + self.n) // hold * hold - self.n
                    y = y[np.clip(idx, 0, len(y) - 1)]
        self.n += len(x)
        out = x * (1 - self.mix) + y.astype(np.float32) * self.mix
        return np.clip(out, -1, 1).astype(np.float32)


def apply(pcm: np.ndarray, preset: str = "none", mix: float = 1.0) -> np.ndarray:
    """Procesa una frase completa y conserva el volumen de pico original."""
    fx = StreamFX(preset or "none", mix)
    if not fx.active or len(pcm) == 0:
        return pcm
    y = fx.process(pcm)
    peak = float(np.max(np.abs(y))) or 1.0
    target = min(0.95, float(np.max(np.abs(pcm))) or 0.95)
    return (y * (target / peak)).astype(np.float32)
