# Mariner — Asistente holográfico de IA

Asistente de vuelo con voz para **Elite Dangerous**, proyectado en un ventilador holográfico.
Arquitectura de *Game Packs* para adaptarlo a otros juegos. Detalles en [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Demo rápida (Windows)

Requisitos: Python 3.11 o superior ([python.org](https://www.python.org/downloads/), marcar *Add to PATH*).

```powershell
cd "C:\Proyectos\Mariner AI"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # y pegar tu OPENAI_API_KEY (opcional para la primera prueba)

python -m mariner --simulate
```

Se abre el navegador con el holograma y un **vuelo simulado** (saltos, interdicción, daño, atraque).
Escribe preguntas en el cuadro inferior izquierdo: *"¿cuánto combustible nos queda?"*, *"dame un reporte"*.

| Comando | Qué hace |
|---|---|
| `python -m mariner --simulate` | Demo sin el juego. Sin API key responde en modo sin conexión. |
| `python -m mariner` | Lee el Journal real de Elite (abrir el juego). |
| `python -m mariner --voice` | Además escucha el micrófono (requiere `OPENAI_API_KEY`). |
| `python -m mariner.tools.mic_test` | Lista micrófonos y mide el nivel para calibrar `VAD_THRESHOLD`. |
| `python -m mariner.tools.enroll --name cristian` | Registra tu voz para que te reconozca (`SPEAKER_ID=true`). |
| `python -m mariner.bridge --target ws://IP_PI:8765/bridge` | En la PC gamer: envía la telemetría a la Raspberry. |
| `python -m mariner.tools.voices` | Escucha las voces (y con `--effects`, los efectos nave/androide/robot) para elegir. |
| `python -m mariner.tools.bench --tts` | Mide tokens, latencia y costo con preguntas típicas (usa tu API key). |
| `python -m mariner.tools.usage_report` | Resumen del consumo real registrado en `data/`. |
| `python -m pytest` | Tests (instalar `pytest` antes). |

En la interfaz: **H** alterna el modo holograma (sin consola ni cursor), **F** pantalla completa, **M** espejo.
URL directa del modo proyector: `http://localhost:8765/?holo=1`.

## Conducta y consumo
- **Perfil** `profiles/<nombre>.toml`: personalidad, largo de respuestas, memoria, voz, avisos y modelos.
  Cada unidad elige el suyo con `PROFILE=` en `.env`.
- **`.env`**: sólo datos de la unidad (`DEVICE_ID`, `OPENAI_API_KEY`, micrófono).
- **Consumo**: cada llamada queda en `data/usage-AAAA-MM.jsonl` y se ve en vivo en la consola de la interfaz.
  Los precios para estimar costos están en `profiles/pricing.toml` (verificarlos).

## Estructura

```
mariner/
  core/        bus de eventos, orquestador, cerebro (OpenAI + tools)
  voice/       micrófono+VAD, transcripción (+ quién habla), voz
  games/       Game Packs — elite_dangerous/ (journal, estado, avisos, simulador)
  ui/          servidor aiohttp + holograma (Canvas)
  bridge.py    PC gamer -> Raspberry
  tools/       utilidades (mic_test, enroll)
deploy/raspberry/   instalación y servicios de arranque autónomo
docs/               arquitectura y decisiones
```

## Agregar un juego nuevo
1. Crear `mariner/games/<id>/__init__.py` con `class Pack(GamePack)`.
2. Implementar `persona()`, `context()`, `tools()`/`call_tool()`, `hud()` y la lectura de telemetría en `start()`.
3. `GAME_PACK=<id>` en `.env`.

## Subir a GitHub
```powershell
git init
git add .
git commit -m "Mariner: MVP asistente holográfico para Elite Dangerous"
git branch -M main
git remote add origin https://github.com/<tu-usuario>/mariner-ai.git
git push -u origin main
```
`.env` y las grabaciones de `voices/` están excluidas por `.gitignore`.
