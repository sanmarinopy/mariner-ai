"""Registra la voz de una persona para que Mariner la reconozca.

  python -m mariner.tools.enroll --name cristian

Graba 8 segundos (el modelo acepta muestras de 2 a 10 s) y guarda voices/<nombre>.wav.
Hablar con naturalidad, como se le hablaría al asistente. Máximo 4 personas.
"""
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

from ..config import load_settings
from ..voice.mic import RATE, to_wav


def main() -> None:
    import sounddevice as sd

    s = load_settings()
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--seconds", type=float, default=8.0)
    a = p.parse_args()
    name = re.sub(r"[^a-z0-9_-]", "", a.name.lower())
    out = Path(s.voices_dir) / f"{name}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)

    print("Lee en voz alta, por ejemplo:")
    print(f'  "{s.assistant_name}, soy {a.name}. Dame el estado de la nave, cuánto combustible')
    print('   nos queda y cuál es el próximo sistema de la ruta."')
    for i in (3, 2, 1):
        print(f"  {i}...", flush=True)
        time.sleep(1)
    print("  ● Grabando")
    rec = sd.rec(int(a.seconds * RATE), samplerate=RATE, channels=1, dtype="float32", device=s.mic_device or None)
    sd.wait()
    out.write_bytes(to_wav(rec[:, 0]))
    print(f"Guardado: {out}\nActiva SPEAKER_ID=true en .env para usarlo.")


if __name__ == "__main__":
    main()
