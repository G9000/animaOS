from __future__ import annotations

import builtins
import importlib
import sys
from types import ModuleType
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

import pytest


def _import_fresh(module_name: str):
    sys.modules.pop(module_name, None)
    sys.modules.pop("anima_core", None)
    sys.modules.pop("anima_core.anima_core", None)
    return importlib.import_module(module_name)


def _import_fresh_preserving_anima_core(module_name: str):
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


@contextmanager
def _patched_anima_core_import(error: BaseException) -> Iterator[None]:
    real_import = builtins.__import__

    def _mock_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "anima_core":
            raise error
        return real_import(name, globals, locals, fromlist, level)

    with patch.object(builtins, "__import__", side_effect=_mock_import):
        yield


def test_text_processing_falls_back_on_import_error() -> None:
    with _patched_anima_core_import(ImportError("mocked")):
        module = _import_fresh("anima_server.services.agent.text_processing")

    assert module._rust_fix_pdf_spacing is None
    assert module._rust_normalize_text is None

    _import_fresh("anima_server.services.agent.text_processing")


def test_text_processing_falls_back_on_non_import_errors() -> None:
    broken_module = ModuleType("anima_core")

    def _broken_getattr(name: str):
        raise RuntimeError("boom")

    broken_module.__getattr__ = _broken_getattr  # type: ignore[attr-defined]

    with patch.dict(sys.modules, {"anima_core": broken_module}):
        module = _import_fresh_preserving_anima_core("anima_server.services.agent.text_processing")

    assert module._rust_fix_pdf_spacing is None
    assert module._rust_normalize_text is None

    _import_fresh("anima_server.services.agent.text_processing")


def test_graph_triplets_falls_back_on_import_error() -> None:
    import anima_server.services.agent.text_processing  # ensure dependency is loaded normally

    with _patched_anima_core_import(ImportError("mocked")):
        module = _import_fresh("anima_server.services.agent.graph_triplets")

    assert module._rust_extract_triplets is None

    _import_fresh("anima_server.services.agent.graph_triplets")


def test_graph_triplets_falls_back_on_non_import_errors() -> None:
    import anima_server.services.agent.text_processing  # ensure dependency is loaded normally

    broken_module = ModuleType("anima_core")

    def _broken_getattr(name: str):
        raise RuntimeError("boom")

    broken_module.__getattr__ = _broken_getattr  # type: ignore[attr-defined]

    with patch.dict(sys.modules, {"anima_core": broken_module}):
        module = _import_fresh_preserving_anima_core("anima_server.services.agent.graph_triplets")

    assert module._rust_extract_triplets is None

    _import_fresh("anima_server.services.agent.graph_triplets")


def test_adaptive_retrieval_falls_back_on_import_error() -> None:
    with _patched_anima_core_import(ImportError("mocked")):
        module = _import_fresh("anima_server.services.agent.adaptive_retrieval")

    assert module._rust_find_adaptive_cutoff is None
    assert module._rust_normalize_scores is None

    _import_fresh("anima_server.services.agent.adaptive_retrieval")


def test_adaptive_retrieval_logs_and_falls_back_on_non_import_errors(caplog: pytest.LogCaptureFixture) -> None:
    broken_module = ModuleType("anima_core")

    def _broken_getattr(name: str):
        raise RuntimeError("boom")

    broken_module.__getattr__ = _broken_getattr  # type: ignore[attr-defined]

    with caplog.at_level("WARNING"):
        with patch.dict(sys.modules, {"anima_core": broken_module}):
            module = _import_fresh_preserving_anima_core("anima_server.services.agent.adaptive_retrieval")

    assert module._rust_find_adaptive_cutoff is None
    assert module._rust_normalize_scores is None
    assert any(
        "adaptive retrieval acceleration" in record.message
        for record in caplog.records
    )

    _import_fresh("anima_server.services.agent.adaptive_retrieval")
