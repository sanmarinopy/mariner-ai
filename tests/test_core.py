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
