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
