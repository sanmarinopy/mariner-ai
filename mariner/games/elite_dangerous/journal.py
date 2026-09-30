"""Lector del Journal de Elite Dangerous.

El juego escribe en:
  %USERPROFILE%\\Saved Games\\Frontier Developments\\Elite Dangerous\\
    Journal.<fecha>.<n>.log   -> una línea JSON por evento (FSDJump, Docked, ...)
    Status.json               -> se reescribe varias veces por segundo (combustible, flags, pips)

Usamos "polling" (cada 0.25 s) en vez de watchers del sistema operativo: es portable
(Windows / Linux / carpeta compartida por SMB) y el costo es despreciable.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

log = logging.getLogger("mariner.elite.journal")

EventCb = Callable[[dict[str, Any]], Awaitable[None]]


class JournalWatcher:
    def __init__(
        self,
        folder: str | Path,
        on_event: EventCb,
        on_status: EventCb,
        interval: float = 0.25,
        replay_last: int = 50,
    ) -> None:
        self.folder = Path(folder)
        self.on_event = on_event
        self.on_status = on_status
        self.interval = interval
        self.replay_last = replay_last  # al arrancar, relee los últimos N eventos para reconstruir estado
        self._file: Path | None = None
        self._pos = 0
        self._buf = ""
        self._status_mtime = 0.0
        self._task: asyncio.Task | None = None
        self._warned = False
        self.last_activity = 0.0  # epoch de la última escritura del juego (journal o Status.json)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="journal-watcher")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    def _latest_journal(self) -> Path | None:
        files = list(self.folder.glob("Journal.*.log"))
        return max(files, key=lambda p: p.stat().st_mtime) if files else None

    async def _run(self) -> None:
        first = True
        while True:
            try:
                if not self.folder.exists():
                    if not self._warned:
                        log.warning("No existe la carpeta del Journal: %s", self.folder)
                        self._warned = True
                else:
                    await self._poll_journal(first)
                    await self._poll_status()
                    first = False
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Error leyendo el Journal")
            await asyncio.sleep(self.interval)

    async def _poll_journal(self, first: bool) -> None:
        latest = self._latest_journal()
        if latest is None:
            return
        self.last_activity = max(self.last_activity, latest.stat().st_mtime)
        if latest != self._file:
            log.info("Siguiendo journal: %s", latest.name)
            self._file, self._pos, self._buf = latest, 0, ""
            if first and self.replay_last:
                # Reconstruir estado con las últimas líneas del archivo actual
                lines = latest.read_text(encoding="utf-8", errors="replace").splitlines()
                for line in lines[-self.replay_last :]:
                    await self._emit_line(line, replay=True)
                self._pos = latest.stat().st_size
                return
        size = self._file.stat().st_size
        if size < self._pos:  # archivo truncado / reescrito
            self._pos = 0
        if size == self._pos:
            return
        with self._file.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(self._pos)
            chunk = fh.read()
            self._pos = fh.tell()
        self._buf += chunk
        *lines, self._buf = self._buf.split("\n")  # la última puede estar incompleta
        for line in lines:
            await self._emit_line(line)

    async def _emit_line(self, line: str, replay: bool = False) -> None:
        line = line.strip()
        if not line:
            return
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            return
        if replay:
            ev["_replay"] = True
        await self.on_event(ev)

    async def _poll_status(self) -> None:
        p = self.folder / "Status.json"
        if not p.exists():
            return
        m = p.stat().st_mtime
        self.last_activity = max(self.last_activity, m)
        if m == self._status_mtime:
            return
        self._status_mtime = m
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return  # el juego lo está escribiendo en este instante; reintentamos en el próximo ciclo
        await self.on_status(data)
