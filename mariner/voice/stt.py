"""Voz -> texto con OpenAI, con identificación opcional de hablante.

Identificación de hablante: si SPEAKER_ID=true y hay archivos en voices/ (p.ej. voices/cristian.wav,
de 2 a 10 s, grabados con `python -m mariner.tools.enroll --name cristian`), se usa el modelo
de diarización con referencias de voz conocidas (hasta 4 personas). El modelo devuelve el nombre
de quien habló; si no coincide con nadie, devuelve una etiqueta genérica (A, B, ...).
"""
from __future__ import annotations

import base64
import logging
import mimetypes
from collections import Counter
from pathlib import Path

from ..config import Settings

log = logging.getLogger("mariner.stt")


class Transcriber:
    def __init__(self, settings: Settings, usage=None) -> None:
        self.usage = usage
        from openai import AsyncOpenAI

        self.s = settings
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.names: list[str] = []
        self.refs: list[str] = []
        if settings.speaker_id:
            self._load_voices(Path(settings.voices_dir))

    def _load_voices(self, folder: Path) -> None:
        for f in sorted(folder.glob("*"))[:4]:
            if f.suffix.lower() not in (".wav", ".mp3", ".m4a", ".ogg", ".webm"):
                continue
            mime = mimetypes.guess_type(f.name)[0] or "audio/wav"
            self.names.append(f.stem)
            self.refs.append(f"data:{mime};base64,{base64.b64encode(f.read_bytes()).decode()}")
        log.info("Voces registradas: %s", ", ".join(self.names) or "(ninguna)")

    @property
    def known(self) -> set[str]:
        return set(self.names)

    async def transcribe(self, wav: bytes) -> tuple[str, str | None]:
        """Devuelve (texto, hablante|None)."""
        file = ("frase.wav", wav, "audio/wav")
        seconds = max(0, len(wav) - 44) / 32000  # WAV 16 kHz, 16 bit, mono
        model = self.s.speaker_model if self.refs else self.s.stt_model
        if self.usage:
            await self.usage.stt(model, seconds)
        if self.refs:
            r = await self.client.audio.transcriptions.create(
                file=file, model=self.s.speaker_model, response_format="diarized_json",
                chunking_strategy="auto", language=self.s.language,
                known_speaker_names=self.names, known_speaker_references=self.refs,
            )
            segs = getattr(r, "segments", None) or []
            text = " ".join(s.text.strip() for s in segs).strip() or (getattr(r, "text", "") or "")
            votes = Counter()
            for s in segs:
                votes[s.speaker] += len(s.text)
            speaker = votes.most_common(1)[0][0] if votes else None
            return text.strip(), speaker
        r = await self.client.audio.transcriptions.create(
            file=file, model=self.s.stt_model, language=self.s.language,
            prompt=f"Conversación con {self.s.assistant_name}, IA de a bordo en Elite Dangerous.",
        )
        return (r.text or "").strip(), None
