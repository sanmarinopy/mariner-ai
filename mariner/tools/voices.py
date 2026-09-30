"""Escucha las voces de OpenAI con la misma frase para elegir la de tu asistente.

  python -m mariner.tools.voices                      # todas las voces
  python -m mariner.tools.voices --only marin,cedar,coral
  python -m mariner.tools.voices --style "Habla relajada, cercana, ritmo natural."

Usa el estilo del perfil salvo que pases --style. Luego pon la elegida en profiles/<perfil>.toml:
  [voice]  voice = "marin"
"""
from __future__ import annotations

import argparse
import asyncio

import numpy as np

from ..config import load_settings
from ..core.events import EventBus
from ..voice.tts import TTS_RATE, Voice

VOICES = ["marin", "cedar", "coral", "nova", "sage", "shimmer", "ballad", "verse",
          "alloy", "ash", "echo", "fable", "onyx"]
PHRASE = ("Comandante, salto completado. Estamos en Wolf 359, con el sesenta y tres por ciento "
          "de combustible. El casco resistió bien. ¿Seguimos la ruta hacia Sirius?")


async def amain(a) -> None:
    import sounddevice as sd

    s = load_settings(a.profile)
    if not s.has_openai:
        raise SystemExit("Falta OPENAI_API_KEY en .env")
    if a.style:
        s.tts_style = a.style
    voices = [v.strip() for v in a.only.split(",")] if a.only else VOICES
    print(f"Estilo: {s.tts_style}\n")
    for v in voices:
        s.tts_voice = v
        voice = Voice(s, EventBus())
        print(f"▶ {v}", flush=True)
        data, _ = await voice.synthesize(a.text)  # queda en caché: repetir no cuesta
        sd.play(np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0, TTS_RATE)
        sd.wait()
        await asyncio.sleep(0.6)
    print("\nElige y ponla en profiles/<perfil>.toml →  [voice] voice = \"...\"")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--profile")
    p.add_argument("--only", help="Voces separadas por comas")
    p.add_argument("--style", help="Instrucción de estilo (reemplaza la del perfil)")
    p.add_argument("--text", default=PHRASE)
    asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    main()
