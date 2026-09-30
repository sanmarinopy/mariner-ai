"""Medidor de consumo de OpenAI.

Registra cada llamada (chat, transcripción, voz) en data/usage-AAAA-MM.jsonl con:
  unidad, perfil, tipo, modelo, tokens (entrada / cacheados / salida / razonamiento),
  minutos de audio y costo estimado según profiles/pricing.toml.
Los tokens son los que devuelve OpenAI (reales). El costo es una estimación.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import Settings


@dataclass
class Totals:
    calls: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    stt_minutes: float = 0.0
    tts_minutes: float = 0.0
    tts_cached: int = 0
    cost: float = 0.0
    cost_unknown: bool = False
    by_kind: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["cost"] = round(self.cost, 5)
        return d


class UsageMeter:
    def __init__(self, settings: Settings, bus=None, write: bool = True) -> None:
        self.s = settings
        self.bus = bus
        self.write = write
        self.total = Totals()
        self.started = time.time()

    # ------------------------------------------------------------------ precios
    def _chat_cost(self, model: str, inp: int, cached: int, out: int) -> float | None:
        p = self.s.pricing.get("chat", {}).get(model)
        if not p or not (p.get("input") or p.get("output")):
            return None
        return ((inp - cached) * p["input"] + cached * p.get("cached", p["input"]) + out * p["output"]) / 1e6

    def _per_min(self, kind: str, model: str, minutes: float) -> float | None:
        price = self.s.pricing.get(kind, {}).get(model)
        return None if not price else minutes * price

    # ------------------------------------------------------------------ registro
    async def chat(self, model: str, usage: Any) -> None:
        if usage is None:
            return
        inp = getattr(usage, "prompt_tokens", 0) or 0
        out = getattr(usage, "completion_tokens", 0) or 0
        cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        reasoning = getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", 0) or 0
        cost = self._chat_cost(model, inp, cached, out)
        t = self.total
        t.input_tokens += inp
        t.cached_tokens += cached
        t.output_tokens += out
        t.reasoning_tokens += reasoning
        await self._record("chat", model, cost, input_tokens=inp, cached_tokens=cached,
                           output_tokens=out, reasoning_tokens=reasoning)

    async def stt(self, model: str, seconds: float) -> None:
        m = seconds / 60
        self.total.stt_minutes += m
        await self._record("stt", model, self._per_min("stt", model, m), audio_seconds=round(seconds, 2))

    async def tts(self, model: str, seconds: float, chars: int, cached: bool) -> None:
        m = seconds / 60
        if cached:
            self.total.tts_cached += 1
            await self._record("tts_cache", model, 0.0, audio_seconds=round(seconds, 2), chars=chars)
            return
        self.total.tts_minutes += m
        await self._record("tts", model, self._per_min("tts", model, m), audio_seconds=round(seconds, 2), chars=chars)

    async def _record(self, kind: str, model: str, cost: float | None, **extra: Any) -> None:
        t = self.total
        t.calls += 1
        t.by_kind[kind] = t.by_kind.get(kind, 0) + 1
        if cost is None:
            t.cost_unknown = True
        else:
            t.cost += cost
        row = {"ts": datetime.now().isoformat(timespec="seconds"), "device": self.s.device_id,
               "profile": self.s.profile, "kind": kind, "model": model,
               "cost_usd": None if cost is None else round(cost, 6), **extra}
        if self.write:
            d = Path(self.s.data_dir)
            d.mkdir(parents=True, exist_ok=True)
            with (d / f"usage-{datetime.now():%Y-%m}.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        if self.bus:
            await self.bus.publish("usage", t.as_dict())
