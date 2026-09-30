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
    assert s.assistant_name == "Mariner" and s.history_turns == 6
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
