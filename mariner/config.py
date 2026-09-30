"""Configuración central.

Tres capas, de menor a mayor prioridad:
  1. valores internos (este archivo)
  2. perfil de conducta: profiles/<PROFILE>.toml  (personalidad, voz, avisos, modelos)
  3. variables de entorno / .env  (secretos y datos de la unidad: API key, DEVICE_ID, micrófono)
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv es opcional
    pass

ROOT = Path(__file__).resolve().parent.parent
PROFILES = ROOT / "profiles"


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _default_journal_dir() -> str:
    # Windows: %USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous
    return str(Path.home() / "Saved Games" / "Frontier Developments" / "Elite Dangerous")


@dataclass
class Settings:
    # --- Unidad ---
    device_id: str = "dev"
    profile: str = "default"

    # --- Identidad y conducta (perfil) ---
    assistant_name: str = "Mariner"
    language: str = "es"
    require_wake_word: bool = False
    personality: str = ""
    max_sentences: int = 3
    max_words: int = 0
    max_reply_tokens: int = 600
    reasoning_effort: str = "low"
    history_turns: int = 6
    engine: str = "pipeline"  # pipeline (voz→texto→IA→voz) | realtime (voz a voz, menor demora)
    realtime_model: str = "gpt-realtime-2.1-mini"
    realtime_reasoning: str = ""
    callouts_enabled: bool = True
    callouts_muted: list[str] = field(default_factory=list)

    # --- Juego ---
    game_pack: str = "elite_dangerous"
    game_source: str = "local"  # local | bridge
    elite_journal_dir: str = ""

    # --- OpenAI ---
    openai_api_key: str = ""
    chat_model: str = "gpt-5.4-mini"
    stt_model: str = "gpt-4o-mini-transcribe"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "nova"
    tts_style: str = ""
    tts_cache: bool = True
    accent: str = ""
    tts_effect: str = "none"
    tts_effect_mix: float = 1.0

    # --- Identificación de hablante ---
    speaker_id: bool = False
    speaker_model: str = "gpt-4o-transcribe-diarize"
    voices_dir: str = str(ROOT / "voices")
    only_known_speakers: bool = False

    # --- Audio ---
    mic_device: str = ""
    vad_threshold: float = 0.015
    vad_silence_ms: int = 600

    # --- Interfaz ---
    host: str = "127.0.0.1"
    port: int = 8765

    # --- Datos locales (consumo, caché de voz) ---
    data_dir: str = str(ROOT / "data")
    pricing: dict[str, Any] = field(default_factory=dict)
    # variables de entorno que pisaron un valor del perfil (para avisar al arrancar)
    env_overrides: list[str] = field(default_factory=list)

    @property
    def has_openai(self) -> bool:
        return bool(self.openai_api_key)


# campo -> (sección del perfil, clave)
_PROFILE_MAP = {
    "assistant_name": ("assistant", "name"),
    "language": ("assistant", "language"),
    "require_wake_word": ("assistant", "require_wake_word"),
    "personality": ("assistant", "personality"),
    "max_sentences": ("assistant", "max_sentences"),
    "max_words": ("assistant", "max_words"),
    "max_reply_tokens": ("assistant", "max_reply_tokens"),
    "reasoning_effort": ("assistant", "reasoning_effort"),
    "history_turns": ("assistant", "history_turns"),
    "engine": ("assistant", "engine"),
    "realtime_model": ("models", "realtime"),
    "realtime_reasoning": ("assistant", "realtime_reasoning"),
    "tts_voice": ("voice", "voice"),
    "tts_style": ("voice", "style"),
    "tts_cache": ("voice", "cache"),
    "accent": ("voice", "accent"),
    "tts_effect": ("voice", "effect"),
    "tts_effect_mix": ("voice", "effect_mix"),
    "callouts_enabled": ("callouts", "enabled"),
    "callouts_muted": ("callouts", "muted"),
    "chat_model": ("models", "chat"),
    "stt_model": ("models", "stt"),
    "tts_model": ("models", "tts"),
    "speaker_model": ("models", "speaker"),
}

# campo -> variable de entorno
_ENV_MAP = {
    "device_id": "DEVICE_ID", "profile": "PROFILE", "engine": "ENGINE",
    "assistant_name": "ASSISTANT_NAME", "language": "LANGUAGE", "require_wake_word": "REQUIRE_WAKE_WORD",
    "game_pack": "GAME_PACK", "game_source": "GAME_SOURCE", "elite_journal_dir": "ELITE_JOURNAL_DIR",
    "openai_api_key": "OPENAI_API_KEY", "chat_model": "OPENAI_CHAT_MODEL", "stt_model": "OPENAI_STT_MODEL",
    "tts_model": "OPENAI_TTS_MODEL", "tts_voice": "OPENAI_TTS_VOICE", "tts_style": "OPENAI_TTS_STYLE",
    "tts_effect": "TTS_EFFECT",
    "speaker_id": "SPEAKER_ID", "speaker_model": "OPENAI_SPEAKER_MODEL", "voices_dir": "VOICES_DIR",
    "only_known_speakers": "ONLY_KNOWN_SPEAKERS", "mic_device": "MIC_DEVICE",
    "vad_threshold": "VAD_THRESHOLD", "vad_silence_ms": "VAD_SILENCE_MS",
    "host": "HOST", "port": "PORT", "data_dir": "DATA_DIR",
}


def _coerce(value: Any, like: Any) -> Any:
    if isinstance(like, bool):
        return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "si", "sí", "on")
    if isinstance(like, int):
        return int(value)
    if isinstance(like, float):
        return float(value)
    if isinstance(like, list):
        return value if isinstance(value, list) else [v.strip() for v in str(value).split(",") if v.strip()]
    return str(value)


def load_settings(profile: str | None = None) -> Settings:
    s = Settings()
    s.profile = profile or os.getenv("PROFILE") or "default"
    prof = _load_toml(PROFILES / f"{s.profile}.toml")
    if not prof and s.profile != "default":
        raise FileNotFoundError(f"No existe el perfil profiles/{s.profile}.toml")
    for name, (sec, key) in _PROFILE_MAP.items():
        if key in prof.get(sec, {}):
            setattr(s, name, _coerce(prof[sec][key], getattr(s, name)))
    for name, env in _ENV_MAP.items():
        v = os.getenv(env)
        if v not in (None, ""):
            new = _coerce(v, getattr(s, name))
            if name in _PROFILE_MAP and new != getattr(s, name) and name != "openai_api_key":
                s.env_overrides.append(f"{env}={v if len(str(v)) < 40 else str(v)[:37] + '...'}")
            setattr(s, name, new)
    if not s.elite_journal_dir:
        s.elite_journal_dir = _default_journal_dir()
    s.pricing = _load_toml(PROFILES / "pricing.toml")
    return s
