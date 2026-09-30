import asyncio
import json
from types import SimpleNamespace as NS

from mariner.config import Settings
from mariner.core.brain import Brain
from mariner.core.events import EventBus
from mariner.games.elite_dangerous import Pack
from mariner.games.elite_dangerous.journal import JournalWatcher
from mariner.games.elite_dangerous.state import ShipState, decode_flags


def test_flags():
    f = decode_flags((1 << 0) | (1 << 19))
    assert f["docked"] and f["low_fuel"] and not f["supercruise"]


def test_state_from_events():
    s = ShipState()
    s.apply_event({"event": "LoadGame", "Commander": "X", "Ship": "Krait_MkII", "FuelLevel": 16, "FuelCapacity": 32})
    s.apply_event({"event": "FSDJump", "StarSystem": "Sol", "JumpDist": 5.2, "FuelLevel": 8})
    assert s.system == "Sol" and s.fuel_pct == 0.25
    s.apply_status({"Flags": 1, "Fuel": {"FuelMain": 4}})
    assert s.docked and s.summary()["combustible_pct"] == 12


def test_journal_watcher(tmp_path):
    got = []

    async def on_ev(e):
        got.append(e["event"])

    async def on_st(s):
        got.append("Status")

    async def run():
        j = tmp_path / "Journal.2026-01-01T000000.01.log"
        j.write_text(json.dumps({"event": "LoadGame"}) + "\n")
        w = JournalWatcher(tmp_path, on_ev, on_st, interval=0.02)
        w.start()
        await asyncio.sleep(0.1)
        with j.open("a") as fh:
            fh.write(json.dumps({"event": "FSDJump"}) + "\n" + '{"event": "Doc')  # línea incompleta
        (tmp_path / "Status.json").write_text('{"Flags": 0}')
        await asyncio.sleep(0.1)
        with j.open("a") as fh:
            fh.write('ked"}\n')
        await asyncio.sleep(0.1)
        await w.stop()

    asyncio.run(run())
    assert got[:2] == ["LoadGame", "FSDJump"] and "Docked" in got and "Status" in got


class FakeCompletions:
    """Simula a OpenAI: primero pide una herramienta, después responde."""

    def __init__(self):
        self.calls = []

    async def create(self, **kw):
        self.calls.append(kw)
        if len(self.calls) == 1:
            tc = NS(id="c1", function=NS(name="get_ship_status", arguments="{}"))
            return NS(choices=[NS(message=NS(content=None, tool_calls=[tc]))])
        return NS(choices=[NS(message=NS(content="Combustible al 50 por ciento.", tool_calls=None))])


def test_brain_tool_loop():
    async def noop(*a):
        pass

    pack = Pack(EventBus(), Settings(), noop)
    pack.state.apply_event({"event": "LoadGame", "FuelLevel": 16, "FuelCapacity": 32})
    b = Brain(Settings(openai_api_key=""), pack)
    fake = FakeCompletions()
    b.client = NS(chat=NS(completions=fake))
    out = asyncio.run(b.ask("¿combustible?"))
    assert out == "Combustible al 50 por ciento."
    second = fake.calls[1]["messages"]
    assert second[-1]["role"] == "tool" and '"combustible_pct": 50' in second[-1]["content"]
    assert len(b.history) == 2


def test_profile_and_env_override(monkeypatch):
    from mariner.config import load_settings

    monkeypatch.setenv("OPENAI_TTS_VOICE", "onyx")
    s = load_settings("default")
    assert s.assistant_name and s.history_turns == 6
    assert s.tts_voice == "onyx"  # el entorno pisa al perfil
    assert "gpt-6-luna" in s.pricing["chat"]


def test_usage_meter_cost(tmp_path):
    from mariner.config import load_settings
    from mariner.core.usage import UsageMeter

    s = load_settings("default")
    s.data_dir = str(tmp_path)
    m = UsageMeter(s)
    u = NS(prompt_tokens=1000, completion_tokens=100,
           prompt_tokens_details=NS(cached_tokens=500), completion_tokens_details=NS(reasoning_tokens=20))
    asyncio.run(m.chat("gpt-6-luna", u))
    # 500*0.05 + 500*0.005 + 100*0.25 = 52.5 por 1M
    assert abs(m.total.cost - 52.5e-6) < 1e-12 and not m.total.cost_unknown
    asyncio.run(m.chat("modelo-sin-precio", u))
    assert m.total.cost_unknown
    assert len(list(tmp_path.glob("usage-*.jsonl"))) == 1


def test_reasoning_effort_fallback():
    import httpx
    from openai import BadRequestError

    calls = []

    class Picky:
        async def create(self, **kw):
            calls.append(kw.get("reasoning_effort"))
            if kw.get("reasoning_effort") != "none":
                req = httpx.Request("POST", "https://x")
                raise BadRequestError("reasoning_effort not supported", response=httpx.Response(400, request=req), body=None)
            return NS(usage=None, choices=[NS(message=NS(content="ok", tool_calls=None))])

    async def noop(*a):
        pass

    s = Settings(openai_api_key="", reasoning_effort="low")
    b = Brain(s, Pack(EventBus(), s, noop))
    b.client = NS(chat=NS(completions=Picky()))
    assert asyncio.run(b.ask("hola")) == "ok"
    assert asyncio.run(b.ask("hola")) == "ok"
    assert calls == ["low", "none", "none"]


def test_voice_effects():
    import numpy as np
    from mariner.voice import effects

    x = (0.5 * np.sin(np.linspace(0, 400, 24000))).astype(np.float32)
    for p in effects.PRESETS:
        y = effects.apply(x, p)
        assert y.shape == x.shape and np.isfinite(y).all() and abs(float(np.abs(y).max()) - 0.5) < 1e-3


def test_env_override_is_reported(monkeypatch):
    from mariner.config import load_settings

    monkeypatch.setenv("OPENAI_TTS_VOICE", "nova")
    s = load_settings("default")
    assert any(o.startswith("OPENAI_TTS_VOICE=nova") for o in s.env_overrides)


def test_brain_streaming_emits_sentences():
    """Streaming: una vuelta con herramienta y otra con texto; las frases salen de a una."""

    def chunk(content=None, tool=None, usage=None, empty=False):
        if empty:
            return NS(choices=[], usage=usage)
        return NS(usage=None, choices=[NS(delta=NS(content=content, tool_calls=[tool] if tool else None))])

    class Stream:
        def __init__(self, items):
            self.items = items

        def __aiter__(self):
            async def gen():
                for i in self.items:
                    yield i
            return gen()

    class FakeStreaming:
        def __init__(self):
            self.n = 0

        async def create(self, **kw):
            assert kw["stream"] is True
            self.n += 1
            if self.n == 1:
                tc = NS(index=0, id="c1", function=NS(name="get_recent_events", arguments='{"count": 2}'))
                return Stream([chunk(tool=tc), chunk(empty=True, usage=None)])
            parts = ["Salto completado, coman", "dante. Combustible al cin", "cuenta por ciento. ", "Todo en orden."]
            return Stream([chunk(p) for p in parts] + [chunk(empty=True)])

    async def noop(*a):
        pass

    s = Settings(openai_api_key="")
    b = Brain(s, Pack(EventBus(), s, noop))
    b.client = NS(chat=NS(completions=FakeStreaming()))
    got = []

    async def emit(x):
        got.append(x)

    out = asyncio.run(b.ask("¿estado?", emit=emit))
    assert got == ["Salto completado, comandante.", "Combustible al cincuenta por ciento.", "Todo en orden."]
    assert out.startswith("Salto completado") and b.history[-1]["content"] == out


def test_streamfx_chunked_equals_whole():
    import numpy as np
    from mariner.voice.effects import StreamFX

    rng = np.random.default_rng(0)
    x = (rng.standard_normal(24000) * 0.2).astype(np.float32)
    for preset in ("nave", "androide", "robot"):
        whole = StreamFX(preset, 0.7).process(x)
        fx = StreamFX(preset, 0.7)
        parts = [fx.process(x[i:i + 1234]) for i in range(0, len(x), 1234)]
        assert np.allclose(np.concatenate(parts), whole, atol=1e-5), preset


def test_realtime_engine_event_flow():
    import base64
    import numpy as np
    from mariner.core.assistant import Assistant
    from mariner.core.realtime import RealtimeEngine
    from mariner.core.usage import UsageMeter

    class FakeConn:
        def __init__(self, events):
            self.events, self.sent = events, []
            self.response = NS(create=self._rec("response.create"))
            self.conversation = NS(item=NS(create=self._rec("item.create")))

        def _rec(self, name):
            async def f(**kw):
                self.sent.append((name, kw))
            return f

        def __aiter__(self):
            async def gen():
                for e in self.events:
                    yield e
            return gen()

    class FakePlayer:
        def __init__(self):
            self.pushed, self.level = [], 0.0

        def push(self, pcm):
            self.pushed.append(pcm)

        busy = False

    audio = base64.b64encode((np.ones(480) * 1000).astype("<i2").tobytes()).decode()
    usage = NS(input_token_details=NS(audio_tokens=100, text_tokens=900, cached_tokens_details=NS(audio_tokens=0, text_tokens=800)),
               output_token_details=NS(audio_tokens=200, text_tokens=30))
    events = [
        NS(type="input_audio_buffer.speech_started"),
        NS(type="input_audio_buffer.speech_stopped"),
        NS(type="input_audio_buffer.committed"),
        NS(type="conversation.item.input_audio_transcription.completed", transcript="¿Qué pasó?"),
        NS(type="response.function_call_arguments.done", name="get_recent_events", arguments='{"count": 1}', call_id="c1"),
        NS(type="response.done", response=NS(usage=usage)),
        NS(type="response.output_audio.delta", delta=audio),
        NS(type="response.output_audio_transcript.done", transcript="Nada grave, comandante."),
        NS(type="response.done", response=NS(usage=usage)),
    ]

    async def run():
        s = Settings(openai_api_key="", tts_effect="androide")
        s.pricing = {"realtime": {"m": dict(audio_in=10, audio_cached=0.3, audio_out=20, text_in=0.6, text_cached=0.06, text_out=2.4)}}
        s.realtime_model = "m"
        bus = EventBus()
        said = []

        async def on(topic, data):
            said.append((topic, data))
        bus.subscribe("assistant.say", on)
        bus.subscribe("user.said", on)
        a = Assistant(s, bus)
        a.pack = Pack(bus, s, a.callout)
        a.brain = Brain(s, a.pack)
        a.usage = UsageMeter(s, write=False)
        e = RealtimeEngine(a)
        e.conn, e.player, e.mic = FakeConn(events), FakePlayer(), NS(muted=False)
        await e._events()
        await asyncio.sleep(0.4)
        return e, a, said

    e, a, said = asyncio.run(run())
    kinds = [k for k, _ in e.conn.sent]
    assert kinds == ["response.create", "item.create", "response.create"]
    assert "MODO ASISTENTE" in e.conn.sent[0][1]["response"]["instructions"]  # sin telemetría
    assert len(e.player.pushed) == 1 and e.mic.muted is False
    assert ("user.said", {"text": "¿Qué pasó?", "speaker": None}) in said
    assert any(d["text"] == "Nada grave, comandante." for k, d in said if k == "assistant.say")
    assert a.usage.total.output_tokens == 460 and a.usage.total.cost > 0


def test_realtime_callout_uses_same_voice():
    from mariner.core.assistant import Assistant
    from mariner.core.realtime import RealtimeEngine

    async def run():
        s = Settings(openai_api_key="")
        a = Assistant(s, EventBus())
        a.pack = Pack(a.bus, s, a.callout)
        a.brain = Brain(s, a.pack)
        e = RealtimeEngine(a)
        sent = []

        async def create(**kw):
            sent.append(kw["response"])
            # el servidor responde: creado → audio → terminado
            asyncio.get_running_loop().call_later(0.05, lambda: e._callout_done.set())

        e.conn = NS(response=NS(create=create))
        e.player = NS(busy=False, push=lambda p: None, level=0.0)
        e.mic = NS(muted=False)
        e._ready.set()
        await e.play(None, "Salto completado.")
        return sent, e

    sent, e = asyncio.run(run())
    r = sent[0]
    assert r["conversation"] == "none" and r["metadata"] == {"kind": "callout"}
    assert "Salto completado." in r["instructions"] and e.mic.muted is False


def test_telemetry_switches_copilot_and_assistant_mode():
    import time as _t

    async def noop(*a):
        pass

    s = Settings(openai_api_key="")
    pack = Pack(EventBus(), s, noop)
    brain = Brain(s, pack)
    pack.watcher = NS(last_activity=_t.time())

    # sin sesión de juego -> asistente
    assert not pack.telemetry_active() and "MODO ASISTENTE" in brain.system_prompt()
    assert pack.hud()["title"] == "Modo asistente"

    # el juego carga -> copiloto
    asyncio.run(pack.on_event({"event": "LoadGame", "Commander": "X", "FuelLevel": 10, "FuelCapacity": 32}))
    assert pack.telemetry_active() and "ESTADO DE LA NAVE" in brain.system_prompt()

    # juego inactivo hace mucho -> asistente
    pack.watcher.last_activity = _t.time() - 3 * 3600
    assert not pack.telemetry_active()

    # cierre del juego -> asistente
    pack.watcher.last_activity = _t.time()
    asyncio.run(pack.on_event({"event": "Shutdown"}))
    assert not pack.telemetry_active()
