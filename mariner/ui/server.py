"""Servidor local: sirve la interfaz del holograma y un WebSocket con los eventos.

  GET  /            -> interfaz (añadir ?holo=1 para modo proyector: sin cursor ni consola)
  WS   /ws          -> la interfaz recibe eventos y puede enviar texto {"type":"say","text":...}
  WS   /bridge      -> la PC gamer envía eventos del juego (ver mariner/bridge.py)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from ..core.events import EventBus

log = logging.getLogger("mariner.ui")
WEB = Path(__file__).parent / "web"
FORWARD = ("assistant.state", "assistant.say", "audio.level", "user.said", "game.hud", "usage")


class UIServer:
    def __init__(self, bus: EventBus, assistant, host: str, port: int) -> None:
        self.bus = bus
        self.assistant = assistant
        self.host, self.port = host, port
        self.clients: set[web.WebSocketResponse] = set()
        self.last: dict[str, Any] = {}  # último valor por tópico, para clientes que se conectan tarde
        for t in FORWARD:
            bus.subscribe(t, self._forward)

    async def _forward(self, topic: str, data: Any) -> None:
        if topic != "audio.level":
            self.last[topic] = data
        msg = json.dumps({"topic": topic, "data": data}, ensure_ascii=False)
        for ws in list(self.clients):
            try:
                await ws.send_str(msg)
            except Exception:
                self.clients.discard(ws)

    async def ws_ui(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        self.clients.add(ws)
        for topic, data in self.last.items():
            await ws.send_str(json.dumps({"topic": topic, "data": data}, ensure_ascii=False))
        try:
            async for m in ws:
                if m.type != WSMsgType.TEXT:
                    continue
                try:
                    msg = json.loads(m.data)
                except json.JSONDecodeError:
                    continue
                if msg.get("type") == "say" and msg.get("text"):
                    import asyncio

                    asyncio.create_task(self.assistant.handle_user_text(str(msg["text"])[:500]))
        finally:
            self.clients.discard(ws)
        return ws

    async def ws_bridge(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        log.info("Bridge conectado desde %s", request.remote)
        async for m in ws:
            if m.type == WSMsgType.TEXT:
                try:
                    await self.bus.publish("bridge.message", json.loads(m.data))
                except json.JSONDecodeError:
                    pass
        log.info("Bridge desconectado")
        return ws

    async def index(self, _request: web.Request) -> web.FileResponse:
        return web.FileResponse(WEB / "index.html")

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/", self.index)
        app.router.add_get("/ws", self.ws_ui)
        app.router.add_get("/bridge", self.ws_bridge)
        app.router.add_static("/static/", WEB)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, self.host, self.port).start()
        log.info("Interfaz en http://%s:%d/", "localhost" if self.host in ("0.0.0.0", "127.0.0.1") else self.host, self.port)
