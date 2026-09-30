"""Compara voces y modelos del MODO TIEMPO REAL (la voz que realmente escuchas al jugar).

  python -m mariner.tools.rt_voices                                  # voces principales, modelo del perfil
  python -m mariner.tools.rt_voices --only cedar,coral --effect none
  python -m mariner.tools.rt_voices --only marin --models gpt-realtime-2.1-mini,gpt-realtime-2.1
  python -m mariner.tools.rt_voices --accent "español rioplatense"
  python -m mariner.tools.rt_voices --only marin --effects none,cabina,androide   # misma muestra, 3 efectos

Cada prueba abre una conexión corta y lee la misma frase. Cuesta unos centavos en total.
"""
from __future__ import annotations

import argparse
import asyncio
import base64

import numpy as np

from ..config import load_settings
from ..voice.effects import StreamFX
from ..voice.speech_rules import voice_rules

VOICES = ["marin", "cedar", "coral", "sage", "shimmer", "ballad", "verse", "alloy", "ash", "echo"]
PHRASE = ("Comandante, salto completado. Estamos en Wolf 359, cerca de la reserva de combustible. "
          "El radar marca tres naves; el casco resistió bien. ¿Seguimos la ruta hacia Sirius?")


async def sample(client, model: str, voice: str, instructions: str, text: str) -> np.ndarray:
    chunks: list[np.ndarray] = []
    async with client.realtime.connect(model=model) as conn:
        await conn.session.update(session={
            "type": "realtime", "output_modalities": ["audio"], "instructions": instructions,
            "audio": {"output": {"format": {"type": "audio/pcm", "rate": 24000}, "voice": voice}},
        })
        await conn.response.create(response={
            "conversation": "none", "input": [],
            "instructions": instructions + "\n\nLee en voz alta exactamente este texto, sin agregar nada.\n"
                                           f"Texto: {text}",
        })
        async for ev in conn:
            if ev.type == "response.output_audio.delta":
                chunks.append(np.frombuffer(base64.b64decode(ev.delta), dtype="<i2").astype(np.float32) / 32768)
            elif ev.type == "response.done":
                break
            elif ev.type == "error":
                raise RuntimeError(getattr(ev.error, "message", ev.error))
    return np.concatenate(chunks) if chunks else np.zeros(1, np.float32)


async def amain(a) -> None:
    import sounddevice as sd
    from openai import AsyncOpenAI

    s = load_settings(a.profile)
    if not s.has_openai:
        raise SystemExit("Falta OPENAI_API_KEY en .env")
    if a.accent:
        s.accent = a.accent
    accent = s.accent
    instructions = voice_rules(s)
    effect = a.effect if a.effect is not None else s.tts_effect
    client = AsyncOpenAI(api_key=s.openai_api_key)
    models = [m.strip() for m in (a.models or s.realtime_model).split(",")]
    voices = [v.strip() for v in a.only.split(",")] if a.only else VOICES[:5]
    print(f"Acento: {accent} · efecto: {effect}\n")
    for m in models:
        for v in voices:
            print(f"▶ {m} · {v}", flush=True)
            try:
                pcm = await sample(client, m, v, instructions, a.text)
            except Exception as e:
                print(f"   error: {e}")
                continue
            for fx in (a.effects.split(",") if a.effects else [effect]):
                fx = fx.strip()
                if a.effects:
                    print(f"   efecto: {fx}", flush=True)
                sd.play(StreamFX(fx, s.tts_effect_mix if fx != "none" else 1.0).process(pcm), 24000)
                sd.wait()
                await asyncio.sleep(0.6)
    print('\nElegí y ponlo en profiles/default.toml → voice = "...", models.realtime = "...", accent = "..."')


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--profile")
    p.add_argument("--only", help="Voces separadas por comas")
    p.add_argument("--models", help="Modelos de tiempo real separados por comas")
    p.add_argument("--accent")
    p.add_argument("--effect", help="none | cabina | nave | androide | robot (por defecto el del perfil)")
    p.add_argument("--effects", help="Varios efectos sobre la MISMA muestra (sin costo extra), ej. none,cabina,androide")
    p.add_argument("--text", default=PHRASE)
    asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    main()
