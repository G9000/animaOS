from __future__ import annotations

import threading

import pytest
from anima_server.services.agent import tools as tools_module


@pytest.fixture(autouse=True)
def _reset_mod_tools_state():
    tools_module.reload_mod_tools()
    tools_module.set_mod_tools_loaded_callback(None)
    yield
    tools_module.reload_mod_tools()
    tools_module.set_mod_tools_loaded_callback(None)


def test_background_refresh_fires_callback_when_tools_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a background fetch newly loads mod tools, the registered
    callback fires so the cached runner can be rebuilt to include them."""
    sentinel = object()

    def fake_fetch() -> list[object]:
        tools_module._mod_tools_cache = [sentinel]
        return [sentinel]

    monkeypatch.setattr(tools_module, "_fetch_and_cache_mod_tools", fake_fetch)

    fired = threading.Event()
    tools_module.set_mod_tools_loaded_callback(fired.set)

    tools_module._start_mod_tools_refresh()

    assert fired.wait(timeout=5), "callback was not fired after mod tools loaded"
    assert tools_module._mod_tools_cache == [sentinel]


def test_background_refresh_skips_callback_when_no_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty fetch (anima-mod down) must not trigger a needless runner
    rebuild."""
    done = threading.Event()

    def fake_fetch() -> list[object]:
        done.set()
        return []

    monkeypatch.setattr(tools_module, "_fetch_and_cache_mod_tools", fake_fetch)

    called = threading.Event()
    tools_module.set_mod_tools_loaded_callback(called.set)

    tools_module._start_mod_tools_refresh()

    assert done.wait(timeout=5)
    # Give the worker a moment to (not) call the callback after fetch returns.
    assert not called.wait(timeout=0.5)


def test_negative_cache_avoids_refetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """After a failure, _load_mod_tools returns [] without re-fetching until
    the TTL elapses (so a down anima-mod can't stall every turn)."""
    calls = {"n": 0}

    def failing_fetch() -> list[object]:
        calls["n"] += 1
        tools_module._mod_tools_last_failure = tools_module._time.monotonic()
        return []

    monkeypatch.setattr(tools_module, "_fetch_and_cache_mod_tools", failing_fetch)
    # Simulate a just-happened failure.
    failing_fetch()
    assert calls["n"] == 1

    # Within the TTL, a sync load short-circuits to [] without re-fetching.
    assert tools_module._load_mod_tools() == []
    assert calls["n"] == 1
