"""Bridge PC gamer -> Raspberry.

El juego corre en Windows; la Raspberry (dentro/junto al holograma) no ve el Journal.
Este proceso liviano corre en la PC, lee el Journal y lo reenvía por WebSocket.

  En la PC:        python -m mariner.bridge --target ws://<ip-raspberry>:8765/bridge
  En la Raspberry: GAME_SOURCE=bridge  y  python -m mariner --host 0.0.0.0
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging

import aiohttp

from .config import load_settings
from .games.elite_dangerous.journal import JournalWatcher

log = logging.getLogger("mariner.bridge")


async def run(target: str, folder: str) -> None:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)

    async def push(kind: str, data: dict) -> None:
        if queue.full():
            queue.get_nowait()
        msg = {"kind": kind, "data": data}
        if kind == "heartbeat":
            msg = {"kind": kind, **data}
        queue.put_nowait(json.dumps(msg))

    watcher = JournalWatcher(folder, lambda e: push("journal", e), lambda s: push("status", s))
    watcher.start()
    log.info("Leyendo %s -> %s", folder, target)

    async def heartbeat() -> None:
        # cada 30 s: cuánto hace que el juego escribió algo (edad relativa: no depende de los relojes)
        import time

        while True:
            await asyncio.sleep(30)
            age = time.time() - watcher.last_activity if watcher.last_activity else 1e9
            await push("heartbeat", {"age_s": round(age, 1)})

    asyncio.create_task(heartbeat())
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.ws_connect(target, heartbeat=20) as ws:
                    log.info("Conectado a Mariner")
                    while True:
                        await ws.send_str(await queue.get())
            except (aiohttp.ClientError, OSError) as e:
                log.warning("Sin conexión con Mariner (%s). Reintento en 3 s", e)
                await asyncio.sleep(3)


def main() -> None:
    s = load_settings()
    p = argparse.ArgumentParser(prog="mariner.bridge")
    p.add_argument("--target", required=True, help="ws://IP:PUERTO/bridge del equipo con Mariner")
    p.add_argument("--journal", default=s.elite_journal_dir)
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    try:
        asyncio.run(run(a.target, a.journal))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
