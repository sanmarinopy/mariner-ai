"""Punto de entrada:  python -m mariner [--simulate] [--voice] [--no-browser]"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import webbrowser

from .config import ROOT, load_settings
from .core.assistant import Assistant
from .core.events import EventBus
from .ui.server import UIServer


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="mariner", description="Mariner — asistente holográfico de vuelo")
    p.add_argument("--simulate", action="store_true", help="Simula un vuelo en Elite (no necesita el juego)")
    p.add_argument("--voice", action="store_true", help="Escucha el micrófono (requiere OPENAI_API_KEY)")
    p.add_argument("--no-browser", action="store_true", help="No abrir el navegador automáticamente")
    p.add_argument("--host", help="IP donde escuchar (0.0.0.0 para aceptar el bridge desde otra PC)")
    p.add_argument("--port", type=int)
    p.add_argument("--profile", help="Perfil de conducta (profiles/<nombre>.toml)")
    p.add_argument("--debug", action="store_true")
    return p.parse_args(argv)


async def amain(args) -> None:
    s = load_settings(args.profile)
    if args.host:
        s.host = args.host
    if args.port:
        s.port = args.port
    bus = EventBus()
    assistant = Assistant(s, bus)

    sim_task = None
    if args.simulate:
        from .games.elite_dangerous.simulator import Simulator

        s.game_pack, s.game_source = "elite_dangerous", "local"
        s.elite_journal_dir = str(ROOT / ".sim_journal")
        sim_task = asyncio.create_task(Simulator(s.elite_journal_dir).run(), name="simulator")

    ui = UIServer(bus, assistant, s.host, s.port)
    await ui.start()
    if not args.no_browser:
        webbrowser.open(f"http://localhost:{s.port}/")
    try:
        await assistant.run(voice_input=args.voice)
    finally:
        if sim_task:
            sim_task.cancel()


def main(argv=None) -> None:
    args = parse_args(argv)
    (ROOT / "data").mkdir(exist_ok=True)
    handlers = [logging.StreamHandler(),
                logging.FileHandler(ROOT / "data" / "mariner.log", mode="w", encoding="utf-8")]
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S",
        handlers=handlers,
    )
    for noisy in ("httpx", "openai", "aiohttp.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        print("\nMariner fuera de línea.", file=sys.stderr)


if __name__ == "__main__":
    main()
