"""Benchmark de consumo: hace preguntas típicas con tu API key y mide tokens, latencia y costo.

  python -m mariner.tools.bench                         # modelo del perfil, sin voz
  python -m mariner.tools.bench --tts                   # incluye síntesis de voz de cada respuesta
  python -m mariner.tools.bench --models gpt-5.4-mini,gpt-6-luna
  python -m mariner.tools.bench --profile otro --per-hour 30

Proyecta el costo por hora de juego y por mes, con los supuestos que se indiquen.
"""
from __future__ import annotations

import argparse
import asyncio
import time

from ..config import load_settings
from ..core.brain import Brain
from ..core.events import EventBus
from ..core.usage import UsageMeter
from ..games.base import load_pack

QUESTIONS = [
    "Hola Mariner, ¿cómo estamos?",
    "¿Cuánto combustible nos queda?",
    "¿En qué sistema estamos?",
    "Dame un reporte completo de la nave.",
    "¿Qué pasó en los últimos saltos?",
    "¿Conviene recargar combustible en una estrella clase M?",
    "¿Qué módulos recomiendas para exploración con una Krait Mk II?",
    "¿Cómo está el casco después del ataque?",
    "Explícame rápido qué es la ingeniería en Elite.",
    "Gracias, Mariner. Seguimos viaje.",
]

SIM_EVENTS = [
    {"event": "LoadGame", "Commander": "Farias", "Ship": "Krait_MkII", "Ship_Localised": "Krait Mk II",
     "ShipName": "Mariner One", "FuelLevel": 30, "FuelCapacity": 32},
    {"event": "Loadout", "Ship": "krait_mkii", "ShipName": "Mariner One", "HullHealth": 1.0,
     "FuelCapacity": {"Main": 32, "Reserve": 0.63}},
    {"event": "FSDJump", "StarSystem": "Barnard's Star", "JumpDist": 6.2, "FuelLevel": 24.1},
    {"event": "FSDJump", "StarSystem": "Wolf 359", "JumpDist": 5.5, "FuelLevel": 20.3},
    {"event": "Interdicted", "Submitted": False, "Interdictor": "Pirata", "IsPlayer": False},
    {"event": "HullDamage", "Health": 0.71, "PlayerPilot": True},
]


async def run_model(model: str, args, base) -> dict:
    s = load_settings(args.profile)
    s.chat_model = model
    s.tts_cache = False
    meter = UsageMeter(s, write=False)

    async def noop(*_):
        pass

    pack = load_pack(s.game_pack, EventBus(), s, noop)
    for ev in SIM_EVENTS:
        pack.state.apply_event(ev)
    brain = Brain(s, pack, meter)
    voice = None
    if args.tts:
        from ..voice.tts import Voice

        voice = Voice(s, EventBus(), meter)

    print(f"\n=== {model} ===")
    print(f"{'#':>2} {'lat s':>6} {'entrada':>8} {'caché':>6} {'salida':>7} {'razon.':>7}  respuesta")
    lat_total = 0.0
    for i, q in enumerate(QUESTIONS[: args.questions], 1):
        before = dict(meter.total.__dict__)
        t0 = time.perf_counter()
        try:
            reply = await brain.ask(q)
        except Exception as e:
            print(f"{i:>2}  ERROR: {type(e).__name__}: {e}")
            return {"model": model, "error": str(e)}
        lat = time.perf_counter() - t0
        lat_total += lat
        if voice:
            await voice.synthesize(reply)
        t = meter.total
        d = lambda k: t.__dict__[k] - before[k]  # noqa: E731
        print(f"{i:>2} {lat:>6.2f} {d('input_tokens'):>8} {d('cached_tokens'):>6} {d('output_tokens'):>7} "
              f"{d('reasoning_tokens'):>7}  {reply[:70]}")

    n = min(args.questions, len(QUESTIONS))
    t = meter.total
    # Oído: se estima (el benchmark no graba audio): segundos por pregunta × precio por minuto
    stt_min_per_q = args.stt_seconds / 60
    stt_price = s.pricing.get("stt", {}).get(s.stt_model, 0)
    chat_cost_known = not t.cost_unknown
    per_q_cost = (t.cost / n) + stt_min_per_q * stt_price
    print(f"\nPromedio por pregunta: {t.input_tokens / n:.0f} tok entrada ({t.cached_tokens / n:.0f} de caché), "
          f"{t.output_tokens / n:.0f} tok salida, {lat_total / n:.2f} s de latencia del modelo")
    if voice:
        print(f"Voz: {t.tts_minutes * 60 / n:.1f} s de audio por respuesta")
    if chat_cost_known:
        hour = per_q_cost * args.per_hour
        print(f"Costo estimado por pregunta: US$ {per_q_cost:.5f}  (incluye oído estimado"
              f"{' y voz' if voice else ', SIN voz: agregue --tts'})")
        print(f"Proyección: {args.per_hour} preguntas/hora → US$ {hour:.4f}/hora → "
              f"US$ {hour * args.hours_month:.2f}/mes con {args.hours_month} h de juego por mes")
    else:
        print("Costo: falta el precio de este modelo en profiles/pricing.toml (los tokens sí son reales).")
    return {"model": model}


async def amain(args) -> None:
    base = load_settings(args.profile)
    if not base.has_openai:
        raise SystemExit("Falta OPENAI_API_KEY en .env")
    models = [m.strip() for m in (args.models or base.chat_model).split(",") if m.strip()]
    print(f"Perfil: {base.profile} · preguntas: {args.questions} · voz: {'sí' if args.tts else 'no'}")
    for m in models:
        await run_model(m, args, base)
    print("\nNota: los avisos automáticos del juego no usan el chat; sólo voz, y se cachean en disco.")


def main() -> None:
    p = argparse.ArgumentParser(prog="mariner.tools.bench")
    p.add_argument("--profile")
    p.add_argument("--models", help="Lista separada por comas (por defecto, el del perfil)")
    p.add_argument("--questions", type=int, default=len(QUESTIONS))
    p.add_argument("--tts", action="store_true", help="Medir también la voz (genera audio, tiene costo)")
    p.add_argument("--per-hour", type=int, default=20, help="Preguntas por hora de juego (supuesto)")
    p.add_argument("--hours-month", type=int, default=40, help="Horas de juego por mes (supuesto)")
    p.add_argument("--stt-seconds", type=float, default=3.0, help="Segundos hablados por pregunta (supuesto)")
    asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    main()
