"""Lista micrófonos y muestra el nivel en vivo para calibrar VAD_THRESHOLD.

  python -m mariner.tools.mic_test            # lista dispositivos
  python -m mariner.tools.mic_test --device 3 # medidor de nivel
"""
from __future__ import annotations

import argparse

import numpy as np


def main() -> None:
    import sounddevice as sd

    p = argparse.ArgumentParser()
    p.add_argument("--device")
    a = p.parse_args()
    if a.device is None:
        print(sd.query_devices())
        print("\nUsa --device N para medir. Luego pon MIC_DEVICE=N en .env")
        return
    dev = int(a.device) if a.device.isdigit() else a.device
    print("Habla y observa el nivel. Ctrl+C para salir. Pon VAD_THRESHOLD entre el silencio y tu voz.")

    def cb(indata, *_):
        rms = float(np.sqrt(np.mean(indata[:, 0] ** 2)))
        print(f"\r{rms:0.4f} " + "█" * int(min(rms * 800, 60)) + " " * 60, end="", flush=True)

    with sd.InputStream(samplerate=16000, channels=1, device=dev, callback=cb, blocksize=480):
        try:
            while True:
                sd.sleep(1000)
        except KeyboardInterrupt:
            print()


if __name__ == "__main__":
    main()
