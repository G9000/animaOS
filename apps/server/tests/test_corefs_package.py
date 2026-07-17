from __future__ import annotations

import builtins
import importlib
import sys


def test_corefs_types_import_does_not_require_native_logical_binding(monkeypatch) -> None:
    original_import = builtins.__import__

    def guarded_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "anima_core":
            raise ImportError("native binding unavailable")
        return original_import(name, *args, **kwargs)

    for module_name in [
        "anima_server.services.corefs",
        "anima_server.services.corefs.logical",
        "anima_server.services.corefs.types",
    ]:
        sys.modules.pop(module_name, None)
    monkeypatch.setattr(builtins, "__import__", guarded_import)

    types = importlib.import_module("anima_server.services.corefs.types")

    assert types.PayloadScope.FS.value == "fs"
