# Mariner — Arquitectura

## 1. Visión

Un asistente de IA con presencia física: una figura en un **ventilador holográfico**, con voz,
que escucha al jugador, reconoce quién le habla y conoce el estado del juego en tiempo real.
Arranca con **Elite Dangerous (Horizons)** y está diseñado para cambiar de juego como producto cerrado
("Game Packs").

```
 PC GAMER (Windows)                         UNIDAD MARINER (Raspberry Pi 5 + holograma)
┌──────────────────────┐                   ┌─────────────────────────────────────────────┐
│ Elite Dangerous      │                   │  mariner (Python, asyncio)                  │
│  └ Journal/*.log     │   WebSocket       │   ├ Game Pack (Elite) ── estado de la nave   │
│  └ Status.json       │ ────────────────▶ │   ├ Brain ── OpenAI (chat + tools)           │
│ mariner.bridge       │   /bridge         │   ├ Oído: mic ▶ VAD ▶ STT (+ quién habla)    │
└──────────────────────┘                   │   ├ Voz: TTS ▶ parlante ▶ nivel de audio     │
                                           │   └ UI server (aiohttp) ── WebSocket /ws     │
                                           │                     │                       │
                                           │  Chromium kiosk (cage) ◀┘  Canvas holograma  │
                                           │         │ HDMI                              │
                                           └─────────┼───────────────────────────────────┘
                                                     ▼
                                          Ventilador holográfico (entrada HDMI)
```

En la demo, todo corre en la misma PC con `GAME_SOURCE=local`.

## 2. Elección de lenguaje

| Opción | A favor | En contra | Veredicto |
|---|---|---|---|
| **Python (núcleo) + HTML5 Canvas/WebGL (interfaz)** | SDK oficial de OpenAI, audio y E/S simples; la interfaz se itera en el navegador y corre igual en Windows y en la Pi; Chromium en la Pi 5 tiene aceleración GPU | Dos lenguajes (Python + JS) | **Elegido** |
| Godot (GDScript/C#) | Motor 2D/3D real y exporta a ARM64 | HTTP/WebSocket y audio con OpenAI más engorrosos; el editor pesa para iterar rápido | Buena opción si el avatar pasa a ser 3D complejo |
| Unity (C#) | Avatares 3D ricos | Pesado para la Pi, licencias, arranque lento | Descartado |
| Rust/C++ (raylib/SDL) | Máximo rendimiento, binario único | Desarrollo mucho más lento para un MVP | Tal vez más adelante para el producto final |
| Electron | Todo en JS | Duplica Chromium y consume mucha RAM en la Pi | Descartado |

La interfaz está separada del núcleo por un protocolo de eventos (WebSocket/JSON). Por eso se
puede reemplazar el Canvas por Three.js, Godot o un binario nativo **sin tocar el núcleo**.

## 3. Componentes

| Módulo | Qué hace |
|---|---|
| `mariner/core/events.py` | Bus pub/sub asíncrono. Todo se comunica por tópicos. |
| `mariner/core/assistant.py` | Orquestador: cola de habla con prioridad, estados (idle/listening/thinking/speaking), silencia el mic mientras habla, descarta avisos viejos. |
| `mariner/core/brain.py` | Chat Completions + *function calling* hacia el Game Pack. Historial corto. Sin API key usa respuestas locales. |
| `mariner/voice/mic.py` | Micrófono + VAD por energía → frases en WAV. |
| `mariner/voice/stt.py` | Transcripción OpenAI. Con `SPEAKER_ID=true` usa `gpt-4o-transcribe-diarize` con muestras de voz registradas (hasta 4) y devuelve **quién habló**. |
| `mariner/voice/tts.py` | Voz OpenAI (`gpt-4o-mini-tts`, con instrucciones de estilo) → PCM → parlante, y publica el nivel de audio para animar la figura. |
| `mariner/games/base.py` | Contrato del Game Pack. |
| `mariner/games/elite_dangerous/` | Lector del Journal/Status.json, modelo de la nave, avisos proactivos, herramientas para la IA y simulador. |
| `mariner/bridge.py` | Reenvía la telemetría del juego de la PC a la Pi. |
| `mariner/ui/` | Servidor aiohttp + interfaz Canvas del holograma. |

### Dos caminos de voz
* **Avisos proactivos** (salto, interdicción, escudos, casco, combustible): plantillas locales, **sin IA**,
  con latencia casi nula y costo cero de tokens. Sólo cuesta la síntesis de voz.
* **Conversación**: mic → STT → LLM (+tools) → TTS. Tarda unos 1,5 a 3 s en total.
  Fase 2: **OpenAI Realtime API** (voz a voz), que baja a menos de 1 s.

## 4. Diseño para ventilador holográfico
* **El negro no se proyecta.** Fondo `#000` puro, sin degradados que "ensucien" el aire.
* **Composición circular**: el área útil es un disco. Todo va dentro de un círculo de radio `0.47 * min(ancho, alto)`.
* **Alto contraste y trazos gruesos**: los LED del ventilador tienen poca resolución angular.
* `?holo=1` oculta la consola y el cursor y fija DPR=1 (rendimiento). `?mirror=1` o tecla **M** espeja la imagen.
* **Verificar al comprar**: muchos ventiladores sólo reproducen videos cargados por app o SD.
  Para contenido **en vivo** hace falta un modelo con **entrada HDMI** o modo *live/sync*.
  Esto es crítico para el proyecto.

## 5. Arranque autónomo (Raspberry)
* **Hardware sugerido**: Raspberry Pi 5 (4 u 8 GB), fuente oficial de 27 W, microSD A2 o SSD NVMe,
  micrófono array USB con cancelación de eco (p.ej. ReSpeaker) y parlante USB o jack.
* **SO**: Raspberry Pi OS **Lite** 64-bit (sin escritorio) + `cage` (compositor Wayland de una sola app) + Chromium `--kiosk`.
* Dos servicios systemd: `mariner.service` (núcleo) y `mariner-kiosk.service` (pantalla). Ver `deploy/raspberry/`.
* Alternativa más liviana a futuro: imagen propia con Buildroot/Yocto (producto cerrado, arranque en pocos segundos).

## 6. Roadmap
1. **MVP (este repo)**: telemetría de Elite, avisos, conversación por texto y voz, holograma 2D, bridge. ✅
2. **Voz natural**: Realtime API, palabra de activación local (openWakeWord) y barge-in (interrumpir al asistente).
3. **Herramientas de juego**: rutas y datos de sistemas (EDSM/Spansh), misiones, mercado, ingeniería.
4. **Acciones en la nave**: el bridge envía teclas (keybinds) con confirmación de voz. Requiere cuidado anti-accidentes.
5. **Avatar**: figura 3D (Three.js/Godot) con estados emocionales y sincronía labial.
6. **Producto**: Game Packs firmados, backend propio que proteja la API key (el cliente no pone su key),
   actualización OTA, licenciamiento. Revisar las políticas de Frontier sobre el uso comercial de la marca Elite Dangerous.
