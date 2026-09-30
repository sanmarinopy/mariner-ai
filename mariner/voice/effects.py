"""Efectos de voz locales (sin costo: se aplican en el equipo, después de generar el audio).

Presets:
  none      voz tal cual
  nave      intercomunicador de cabina: eco metálico corto y banda de radio amplia
  androide  IA de nave: modulación sutil + resonancia metálica
  robot     computadora clásica: modulación fuerte, eco corto y algo de "bits"

Se puede ajustar la intensidad con effect_mix (0..1) en el perfil.
"""
from __future__ import annotations

import numpy as np

RATE = 24000


def _delay(x: np.ndarray, ms: float) -> np.ndarray:
    d = max(1, int(RATE * ms / 1000))
    y = np.zeros_like(x)
    if d < len(x):
        y[d:] = x[:-d]
    return y


def _comb(x: np.ndarray, ms: float, g: float, taps: int = 5) -> np.ndarray:
    """Resonancia metálica: suma de ecos muy cortos que decaen."""
    y = x.copy()
    for k in range(1, taps + 1):
        y += (g ** k) * _delay(x, ms * k)
    return y


def _band(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Filtro pasa-banda suave (tipo radio)."""
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / RATE)
    f[0] = 1e-3
    gain = 1 / (1 + (lo / f) ** 4) * 1 / (1 + (f / hi) ** 4)
    return np.fft.irfft(X * gain, n=len(x)).astype(np.float32)


def _ring(x: np.ndarray, hz: float, mix: float) -> np.ndarray:
    """Modulación en anillo: el timbre "metálico/robótico" característico."""
    t = np.arange(len(x)) / RATE
    return x * (1 - mix) + x * np.sin(2 * np.pi * hz * t).astype(np.float32) * mix


def _crush(x: np.ndarray, bits: int, hold: int) -> np.ndarray:
    """Baja la resolución: sonido digital."""
    q = 2 ** (bits - 1)
    y = np.round(x * q) / q
    if hold > 1:
        y = np.repeat(y[::hold], hold)[: len(x)]
    return y.astype(np.float32)


PRESETS: dict[str, list[tuple]] = {
    "none": [],
    "nave": [("comb", 7, 0.30), ("band", 140, 7000)],
    "androide": [("ring", 38, 0.30), ("comb", 4.5, 0.40), ("band", 200, 6000)],
    "robot": [("ring", 85, 0.65), ("comb", 3, 0.50), ("crush", 10, 2), ("band", 220, 4500)],
}


def apply(pcm: np.ndarray, preset: str = "none", mix: float = 1.0) -> np.ndarray:
    """pcm: float32 mono 24 kHz en [-1, 1]. Devuelve el audio procesado."""
    chain = PRESETS.get(preset or "none")
    if chain is None:
        raise ValueError(f"Efecto desconocido: {preset}. Opciones: {', '.join(PRESETS)}")
    if not chain or len(pcm) == 0:
        return pcm
    y = pcm.astype(np.float32).copy()
    for op, *p in chain:
        if op == "comb":
            y = _comb(y, *p)
        elif op == "band":
            y = _band(y, *p)
        elif op == "ring":
            y = _ring(y, *p)
        elif op == "crush":
            y = _crush(y, *p)
    mix = min(1.0, max(0.0, mix))
    y = pcm * (1 - mix) + y * mix
    # mismo volumen de pico que la voz original
    peak = float(np.max(np.abs(y))) or 1.0
    target = min(0.95, float(np.max(np.abs(pcm))) or 0.95)
    return (y * (target / peak)).astype(np.float32)
