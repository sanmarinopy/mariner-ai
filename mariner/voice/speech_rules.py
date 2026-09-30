"""Reglas de pronunciación que se envían al modelo de voz.

Los modelos de voz siguen mejor indicaciones fonéticas concretas que un simple "sin acento".
Van al principio de las instrucciones porque es lo que más peso recibe.
"""
from __future__ import annotations

from ..config import Settings

PRONUNCIACION_ES = (
    "Pronunciación de hablante nativo de español: "
    "la r entre vocales es una vibrante simple y suave (pero, caro, cero); "
    "la rr y la r al inicio de palabra o después de n, l o s son vibrantes múltiples (radar, carro, "
    "honra, alrededor, Israel); nunca pronuncies la r como en inglés o francés, ni gutural. "
    "Vocales puras, cortas y claras (a, e, i, o, u), sin diptongarlas. "
    "La t, la p y la k sin aspirar; la d entre vocales suave. "
    "Ritmo silábico parejo del español, sin entonación de alguien que aprende el idioma."
)


def voice_rules(s: Settings) -> str:
    accent = s.accent or "español latinoamericano neutro"
    parts = [f"IDIOMA Y PRONUNCIACIÓN: habla siempre en {accent}."]
    if (s.language or "es").startswith("es"):
        parts.append(PRONUNCIACION_ES)
    if s.tts_style:
        parts.append(f"Estilo de voz: {s.tts_style}")
    return " ".join(parts)
