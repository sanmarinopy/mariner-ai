"""Configuración central. Todo se lee de variables de entorno / archivo .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv es opcional
    pass

ROOT = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "si", "sí", "on")


def _default_journal_dir() -> str:
    # Windows: %USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous
    return str(Path.home() / "Saved Games" / "Frontier Developments" / "Elite Dangerous")


@dataclass
class Settings:
    # --- Identidad del asistente ---
    assistant_name: str = os.getenv("ASSISTANT_NAME", "Mariner")
    language: str = os.getenv("LANGUAGE", "es")
    # Si está activo, sólo responde cuando la frase contiene el nombre del asistente.
    require_wake_word: bool = _bool("REQUIRE_WAKE_WORD", False)

    # --- Juego ---
    game_pack: str = os.getenv("GAME_PACK", "elite_dangerous")
    # local = lee el Journal en esta máquina; bridge = recibe eventos desde la PC gamer
    game_source: str = os.getenv("GAME_SOURCE", "local")
    elite_journal_dir: str = os.getenv("ELITE_JOURNAL_DIR", "") or _default_journal_dir()

    # --- OpenAI ---
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    chat_model: str = os.getenv("OPENAI_CHAT_MODEL", "gpt-5.4-mini")
    stt_model: str = os.getenv("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")
    tts_model: str = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
    tts_voice: str = os.getenv("OPENAI_TTS_VOICE", "nova")
    tts_style: str = os.getenv(
        "OPENAI_TTS_STYLE",
        "Voz de IA de a bordo: calma, precisa, levemente cálida. Frases cortas, tono de cabina.",
    )

    # --- Identificación de hablante (opcional) ---
    speaker_id: bool = _bool("SPEAKER_ID", False)
    speaker_model: str = os.getenv("OPENAI_SPEAKER_MODEL", "gpt-4o-transcribe-diarize")
    voices_dir: str = os.getenv("VOICES_DIR", str(ROOT / "voices"))
    # Si está activo, ignora a quien no esté registrado en voices/
    only_known_speakers: bool = _bool("ONLY_KNOWN_SPEAKERS", False)

    # --- Audio ---
    mic_device: str = os.getenv("MIC_DEVICE", "")  # vacío = dispositivo por defecto
    vad_threshold: float = float(os.getenv("VAD_THRESHOLD", "0.015"))
    vad_silence_ms: int = int(os.getenv("VAD_SILENCE_MS", "900"))

    # --- Interfaz ---
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8765"))

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key)


def load_settings() -> Settings:
    return Settings()
