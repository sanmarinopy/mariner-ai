"""Resumen del consumo real registrado en data/usage-*.jsonl.

  python -m mariner.tools.usage_report            # todo, agrupado por día
  python -m mariner.tools.usage_report --by device
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from ..config import load_settings


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--by", choices=["day", "device", "profile", "model"], default="day")
    a = p.parse_args()
    s = load_settings()
    rows = []
    for f in sorted(Path(s.data_dir).glob("usage-*.jsonl")):
        rows += [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not rows:
        print(f"Sin registros en {s.data_dir}")
        return
    agg = defaultdict(lambda: defaultdict(float))
    for r in rows:
        key = r["ts"][:10] if a.by == "day" else r.get(a.by, "?")
        g = agg[key]
        g["chat"] += r["kind"] == "chat"
        g["in"] += r.get("input_tokens", 0)
        g["cached"] += r.get("cached_tokens", 0)
        g["out"] += r.get("output_tokens", 0)
        g["stt_min"] += r.get("audio_seconds", 0) / 60 if r["kind"] == "stt" else 0
        g["tts_min"] += r.get("audio_seconds", 0) / 60 if r["kind"] == "tts" else 0
        g["tts_cache"] += r["kind"] == "tts_cache"
        g["cost"] += r.get("cost_usd") or 0
        g["unknown"] += r.get("cost_usd") is None
    print(f"{a.by:<14}{'chats':>6}{'tok ent':>9}{'caché':>8}{'tok sal':>9}{'oído min':>9}{'voz min':>8}{'voz caché':>10}{'US$':>10}")
    for k in sorted(agg):
        g = agg[k]
        flag = "*" if g["unknown"] else ""
        print(f"{k:<14}{g['chat']:>6.0f}{g['in']:>9.0f}{g['cached']:>8.0f}{g['out']:>9.0f}"
              f"{g['stt_min']:>9.2f}{g['tts_min']:>8.2f}{g['tts_cache']:>10.0f}{g['cost']:>9.4f}{flag}")
    if any(g["unknown"] for g in agg.values()):
        print("* hay llamadas sin precio en profiles/pricing.toml: el costo real es mayor.")


if __name__ == "__main__":
    main()
