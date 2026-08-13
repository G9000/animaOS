from __future__ import annotations

import pytest
from anima_server.services.corefs.diary_migration import migration_opaque_id
from anima_server.services.corefs.formats import (
    CoreFormatError,
    decode_preferences_document,
    encode_preferences_document,
)


def test_preferences_are_canonical_json_and_round_trip_portable_values() -> None:
    stable_id = migration_opaque_id("preferences", "owner-1")
    first = encode_preferences_document(
        stable_id=stable_id,
        owner_id="owner-1",
        values={
            "theme": "dark",
            "translateLanguage": "ms",
            "ascii": {"enabled": True, "density": 0.5},
            "presence": {"enabled": True},
        },
        updated_at="2026-08-13T08:00:00+00:00",
    )
    second = encode_preferences_document(
        stable_id=stable_id,
        owner_id="owner-1",
        values={
            "presence": {"enabled": True},
            "ascii": {"density": 0.5, "enabled": True},
            "translateLanguage": "ms",
            "theme": "dark",
        },
        updated_at="2026-08-13T08:00:00+00:00",
    )

    assert first == second
    decoded = decode_preferences_document(first)
    assert decoded.owner_id == "owner-1"
    assert decoded.values["theme"] == "dark"
    assert decoded.values["ascii"] == {"density": 0.5, "enabled": True}


def test_preferences_reject_non_json_or_non_finite_values() -> None:
    stable_id = migration_opaque_id("preferences", "owner-1")
    with pytest.raises(CoreFormatError, match="canonical JSON"):
        encode_preferences_document(
            stable_id=stable_id,
            owner_id="owner-1",
            values={"bad": object()},
            updated_at=None,
        )
    with pytest.raises(CoreFormatError, match="canonical JSON"):
        encode_preferences_document(
            stable_id=stable_id,
            owner_id="owner-1",
            values={"bad": float("nan")},
            updated_at=None,
        )
