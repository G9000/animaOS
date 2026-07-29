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

    module_names = [
        "anima_server.services.corefs",
        "anima_server.services.corefs.logical",
        "anima_server.services.corefs.types",
    ]
    # Snapshot the already-loaded modules so they can be RESTORED afterwards.
    # Popping them permanently makes any later import re-execute corefs.types,
    # minting DUPLICATE enum classes (PayloadScope/KeyslotStatus) while
    # earlier-loaded modules (keyslots.py etc.) still hold the originals —
    # identity comparisons like `slot.status is status` then match nothing,
    # and every subsequent keyslot/recovery/vault test in the same process
    # fails with "ambiguous scope" 401s (MIH-003: this single leak poisoned
    # 21 tests at full-suite scale).
    saved = {name: sys.modules.get(name) for name in module_names}
    try:
        for module_name in module_names:
            sys.modules.pop(module_name, None)
        monkeypatch.setattr(builtins, "__import__", guarded_import)

        types = importlib.import_module("anima_server.services.corefs.types")

        assert types.PayloadScope.FS.value == "fs"
    finally:
        for module_name in module_names:
            sys.modules.pop(module_name, None)
        for module_name, module in saved.items():
            if module is not None:
                sys.modules[module_name] = module
        # Importing the fresh corefs.types also re-bound the `corefs`
        # ATTRIBUTE on the parent `anima_server.services` package object
        # (Python binds submodules on their parent at import). sys.modules
        # restoration alone doesn't undo that, and attribute-walking
        # resolvers (pytest's monkeypatch string targets) would still reach
        # the fresh, lazily-empty package. Rebind the original.
        parent = sys.modules.get("anima_server.services")
        original_corefs = saved.get("anima_server.services.corefs")
        if parent is not None:
            if original_corefs is not None:
                parent.corefs = original_corefs
            elif hasattr(parent, "corefs"):
                # Nothing was loaded before the test: leaving the FRESH package
                # bound here (with sys.modules cleaned) would hand
                # attribute-walkers a different object than a later re-import
                # creates — the exact split-module state this cleanup prevents.
                del parent.corefs
