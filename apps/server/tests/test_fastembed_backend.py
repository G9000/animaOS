from __future__ import annotations

import pytest
from anima_server.services.agent import fastembed_backend


@pytest.fixture(autouse=True)
def reset_backend():
    fastembed_backend._reset_backend_for_tests()
    yield
    fastembed_backend._reset_backend_for_tests()


class _FakeModel:
    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.calls: list[list[str]] = []

    def embed(self, texts):
        self.calls.append(list(texts))
        for index, _text in enumerate(texts):
            yield [float(index + 1)] * self.dim


def test_embed_texts_returns_vectors(monkeypatch) -> None:
    fake = _FakeModel()
    monkeypatch.setattr(fastembed_backend, "_create_model", lambda model_name: fake)

    vectors = fastembed_backend.embed_texts(["alpha", "beta"], model_name="test-model")

    assert vectors == [[1.0] * 4, [2.0] * 4]
    assert fake.calls == [["alpha", "beta"]]


def test_model_loads_once_and_is_reused(monkeypatch) -> None:
    created: list[str] = []

    def factory(model_name: str):
        created.append(model_name)
        return _FakeModel()

    monkeypatch.setattr(fastembed_backend, "_create_model", factory)

    fastembed_backend.embed_texts(["a"], model_name="test-model")
    fastembed_backend.embed_texts(["b"], model_name="test-model")

    assert created == ["test-model"]


def test_load_failure_degrades_to_none_and_fast_fails(monkeypatch) -> None:
    calls: list[str] = []

    def factory(model_name: str):
        calls.append(model_name)
        raise RuntimeError("onnx model missing")

    monkeypatch.setattr(fastembed_backend, "_create_model", factory)

    first = fastembed_backend.embed_texts(["a"], model_name="test-model")
    second = fastembed_backend.embed_texts(["b"], model_name="test-model")

    assert first == [None]
    assert second == [None]
    assert calls == ["test-model"]  # failed flag prevents reload storm


def test_load_failure_retries_after_ttl_expires(monkeypatch) -> None:
    calls: list[str] = []
    clock = {"now": 1000.0}

    def factory(model_name: str):
        calls.append(model_name)
        raise RuntimeError("onnx model missing")

    monkeypatch.setattr(fastembed_backend, "_create_model", factory)
    monkeypatch.setattr(fastembed_backend.time, "monotonic", lambda: clock["now"])

    first = fastembed_backend.embed_texts(["a"], model_name="test-model")
    assert first == [None]
    assert calls == ["test-model"]

    # Still within the TTL window: no reload attempt.
    clock["now"] += fastembed_backend._RETRY_TTL_SECONDS - 1
    second = fastembed_backend.embed_texts(["b"], model_name="test-model")
    assert second == [None]
    assert calls == ["test-model"]

    # TTL has expired: a fresh load is attempted (and re-stamps on failure).
    clock["now"] += 2
    fake = _FakeModel()
    monkeypatch.setattr(fastembed_backend, "_create_model", lambda model_name: fake)
    third = fastembed_backend.embed_texts(["c"], model_name="test-model")
    assert third == [[1.0] * 4]


# ── backend_status: model-name-aware ────────────────────────────────────
#
# backend_status() must not just check "is *some* model loaded" — it has to
# agree with which model is currently configured. Otherwise a failed switch
# from model A to model B leaves a stale, no-longer-relevant model A sitting
# in `_model`, and backend_status would keep reporting "ready" even though
# `embed_texts(model_name="B")` is actively returning None vectors.


def test_backend_status_cold_when_nothing_loaded(monkeypatch) -> None:
    monkeypatch.setattr(fastembed_backend, "_resolve_current_model_name", lambda: "model-a")
    assert fastembed_backend.backend_status() == "cold"


def test_backend_status_ready_when_loaded_model_matches_current(monkeypatch) -> None:
    monkeypatch.setattr(fastembed_backend, "_resolve_current_model_name", lambda: "model-a")
    fake = _FakeModel()
    monkeypatch.setattr(fastembed_backend, "_create_model", lambda model_name: fake)

    fastembed_backend.embed_texts(["a"], model_name="model-a")

    assert fastembed_backend.backend_status() == "ready"


def test_backend_status_cold_when_loaded_model_differs_from_current_no_failure(
    monkeypatch,
) -> None:
    fake = _FakeModel()
    monkeypatch.setattr(fastembed_backend, "_create_model", lambda model_name: fake)
    fastembed_backend.embed_texts(["a"], model_name="model-a")

    # Config has since moved on to model-b; model-a was never re-attempted
    # for model-b, so there's no active failure — just a stale load.
    monkeypatch.setattr(fastembed_backend, "_resolve_current_model_name", lambda: "model-b")

    assert fastembed_backend.backend_status() == "cold"


def test_backend_status_failed_retrying_when_switch_to_current_model_fails(
    monkeypatch,
) -> None:
    """Model A loaded fine; switching to model B fails and latches within
    TTL. Status must reflect B's failure, not A's stale "ready" state."""
    clock = {"now": 2000.0}
    monkeypatch.setattr(fastembed_backend.time, "monotonic", lambda: clock["now"])

    fake = _FakeModel()
    monkeypatch.setattr(fastembed_backend, "_create_model", lambda model_name: fake)
    fastembed_backend.embed_texts(["a"], model_name="model-a")

    monkeypatch.setattr(fastembed_backend, "_resolve_current_model_name", lambda: "model-a")
    assert fastembed_backend.backend_status() == "ready"

    def failing_factory(model_name: str):
        raise RuntimeError("model-b unavailable")

    monkeypatch.setattr(fastembed_backend, "_create_model", failing_factory)
    monkeypatch.setattr(fastembed_backend, "_resolve_current_model_name", lambda: "model-b")

    result = fastembed_backend.embed_texts(["x"], model_name="model-b")
    assert result == [None]

    assert fastembed_backend.backend_status() == "failed_retrying"

    # Still within TTL: stays failed_retrying, not "ready" from stale model A.
    clock["now"] += fastembed_backend._RETRY_TTL_SECONDS - 1
    assert fastembed_backend.backend_status() == "failed_retrying"

    # TTL lapsed with no retry yet attempted: cold (model A is stale for the
    # now-current model B), implying a retry is due on the next embed call.
    clock["now"] += 2
    assert fastembed_backend.backend_status() == "cold"


# ── FINDING B: cooldown must be keyed by model name ──────────────────────
#
# The `_failed_at` TTL latch used to be global: one model's load failure
# blocked every OTHER model name for the full 300s cooldown too. That means
# fixing a mistyped embedding model in the settings UI still yielded no
# dense embeddings for 5 minutes, since the new (correct) model name got
# blocked by the old (broken) one's cooldown. The fix keys the cooldown to
# the specific model name that failed via `_failed_model_name`.


def test_other_model_gets_fresh_attempt_despite_different_models_cooldown(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def factory(model_name: str):
        calls.append(model_name)
        if model_name == "model-a":
            raise RuntimeError("model-a unavailable")
        return _FakeModel()

    monkeypatch.setattr(fastembed_backend, "_create_model", factory)

    # model-a fails and latches its own cooldown.
    first = fastembed_backend.embed_texts(["x"], model_name="model-a")
    assert first == [None]
    assert calls == ["model-a"]

    # model-b is a completely different model — it must get a fresh attempt
    # immediately, not be blocked by model-a's cooldown.
    second = fastembed_backend.embed_texts(["y"], model_name="model-b")
    assert second == [[1.0] * 4]
    assert calls == ["model-a", "model-b"]


def test_same_model_still_blocked_within_ttl_after_own_failure_even_after_other_succeeds(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def factory(model_name: str):
        calls.append(model_name)
        if model_name == "model-a":
            raise RuntimeError("model-a unavailable")
        return _FakeModel()

    monkeypatch.setattr(fastembed_backend, "_create_model", factory)

    fastembed_backend.embed_texts(["x"], model_name="model-a")
    fastembed_backend.embed_texts(["y"], model_name="model-b")
    assert calls == ["model-a", "model-b"]

    # model-a is retried within its own TTL window: still blocked (no new
    # _create_model call for it), because model-b's unrelated success must
    # not clear model-a's own latch.
    third = fastembed_backend.embed_texts(["z"], model_name="model-a")
    assert third == [None]
    assert calls == ["model-a", "model-b"]


def test_successful_load_clears_its_own_latch(monkeypatch) -> None:
    calls: list[str] = []
    clock = {"now": 5000.0}
    monkeypatch.setattr(fastembed_backend.time, "monotonic", lambda: clock["now"])

    def failing_factory(model_name: str):
        calls.append(model_name)
        raise RuntimeError("model-a unavailable")

    monkeypatch.setattr(fastembed_backend, "_create_model", failing_factory)
    first = fastembed_backend.embed_texts(["x"], model_name="model-a")
    assert first == [None]

    # model-a's own TTL has elapsed and it recovers — a successful retry
    # must clear its own latch entirely (not just stop returning None).
    clock["now"] += fastembed_backend._RETRY_TTL_SECONDS + 1
    fake = _FakeModel()
    monkeypatch.setattr(fastembed_backend, "_create_model", lambda model_name: fake)
    second = fastembed_backend.embed_texts(["y"], model_name="model-a")
    assert second == [[1.0] * 4]
    assert fastembed_backend._failed_at is None
    assert fastembed_backend._failed_model_name is None


def test_backend_status_not_failed_retrying_for_differently_latched_model(
    monkeypatch,
) -> None:
    """A failure latched for model-a must not make backend_status() report
    failed_retrying when the currently-resolved model is model-b — that
    would tell the UI dense embeddings are broken for a model that was
    never even attempted."""
    clock = {"now": 4000.0}
    monkeypatch.setattr(fastembed_backend.time, "monotonic", lambda: clock["now"])

    def factory(model_name: str):
        if model_name == "model-a":
            raise RuntimeError("model-a unavailable")
        return _FakeModel()

    monkeypatch.setattr(fastembed_backend, "_create_model", factory)
    fastembed_backend.embed_texts(["x"], model_name="model-a")

    monkeypatch.setattr(fastembed_backend, "_resolve_current_model_name", lambda: "model-b")
    assert fastembed_backend.backend_status() == "cold"
