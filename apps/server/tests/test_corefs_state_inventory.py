from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import anima_server.models
import anima_server.models.corefs_runtime
import anima_server.models.runtime_memory  # noqa: F401 - registers Runtime metadata
from anima_server.config import _PERSISTED_RUNTIME_SETTING_FIELDS
from anima_server.db.base import Base
from anima_server.db.runtime_base import RuntimeBase

REPO_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_PATH = REPO_ROOT / "docs/architecture/system/portable-state-inventory.md"
START = "<!-- portable-state-inventory:v1:start -->"
END = "<!-- portable-state-inventory:v1:end -->"


@dataclass(frozen=True, slots=True)
class InventoryRecord:
    store: str
    record: str
    fields: tuple[str, ...]
    destination: str


def _inventory() -> tuple[InventoryRecord, ...]:
    document = INVENTORY_PATH.read_text(encoding="utf-8")
    assert document.count(START) == 1
    assert document.count(END) == 1
    body = document.split(START, 1)[1].split(END, 1)[0]
    records: list[InventoryRecord] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```"):
            continue
        parts = line.split("|")
        assert len(parts) == 4, f"invalid inventory row: {line}"
        store, record, raw_fields, destination = parts
        fields = tuple(field.strip() for field in raw_fields.split(","))
        assert store and record and destination
        assert fields and all(fields)
        assert len(fields) == len(set(fields)), f"duplicate fields in {store}.{record}"
        records.append(
            InventoryRecord(
                store=store,
                record=record,
                fields=fields,
                destination=destination,
            )
        )
    assert records
    return tuple(records)


def _assert_schema_is_exactly_classified(
    store: str,
    tables: dict[str, object],
) -> None:
    records = [record for record in _inventory() if record.store == store]
    classified: dict[str, list[str]] = defaultdict(list)
    for record in records:
        classified[record.record].extend(record.fields)

    assert set(classified) == set(tables)
    for table_name, table in tables.items():
        expected = [column.name for column in table.columns]  # type: ignore[attr-defined]
        actual = classified[table_name]
        assert len(actual) == len(set(actual)), (
            f"{store}.{table_name} classifies one or more fields more than once"
        )
        assert set(actual) == set(expected), (
            f"{store}.{table_name} inventory drift; "
            f"missing={sorted(set(expected) - set(actual))}, "
            f"unknown={sorted(set(actual) - set(expected))}"
        )


def _destinations(store: str, record: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in _inventory():
        if row.store != store or row.record != record:
            continue
        for field in row.fields:
            assert field not in result
            result[field] = row.destination
    return result


def test_every_sqlcipher_table_and_field_has_one_approved_destination() -> None:
    _assert_schema_is_exactly_classified("sqlcipher", dict(Base.metadata.tables))


def test_every_runtime_table_and_field_has_one_approved_destination() -> None:
    _assert_schema_is_exactly_classified("runtime-db", dict(RuntimeBase.metadata.tables))


def test_required_mixed_rows_are_classified_field_by_field() -> None:
    agent_profile = _destinations("sqlcipher", "agent_profile")
    self_model = _destinations("sqlcipher", "self_model_blocks")
    episode = _destinations("sqlcipher", "memory_episodes")
    user = _destinations("sqlcipher", "users")

    assert agent_profile["setup_complete"] == "account-profile"
    assert agent_profile["agent_name"] == "soul"
    assert self_model["content"] == "soul"
    assert self_model["needs_regeneration"] == "runtime-machine-local"
    assert episode["summary"] == "soul"
    assert episode["needs_regeneration"] == "runtime-machine-local"
    assert user["username"] == "account-profile"
    assert user["password_hash"] == "remove-legacy-auth"


def test_persisted_runtime_setting_whitelist_has_no_unclassified_field() -> None:
    destinations = _destinations("server-config", "runtime-config")
    assert set(destinations) == set(_PERSISTED_RUNTIME_SETTING_FIELDS)
    assert destinations["agent_api_key"] == "os-credential"
    assert destinations["agent_api_keys_json"] == "os-credential"
    assert destinations["agent_embedding_api_key"] == "os-credential"


def test_browser_secrets_and_private_profile_are_never_portable_destinations() -> None:
    local = _destinations("browser-local", "keys")
    assert local["anima_daemon_control_token"] == "os-credential"
    assert local["ANIMA_DAEMON_CONTROL_TOKEN"] == "os-credential"
    assert local["anima_unlock_token"] == "remove-legacy-session"
    assert local["anima_user"] == "remove-private-profile-cache"
    assert local["anima_last_user"] == "remove-private-profile-cache"
    assert "legacy-journal-draft:*" not in local
    assert "anima:diary:draft-migration-state:v1:*" not in local


def test_mod_secret_sources_have_explicit_credential_destinations() -> None:
    google_tokens = _destinations("anima-mod-store", "google:tokens:*")
    assert set(google_tokens) == {"accessToken", "refreshToken", "expiresAt", "email"}
    assert set(google_tokens.values()) == {"os-credential"}


def test_machine_local_registry_files_have_explicit_nonportable_destinations() -> None:
    files = _destinations("app-data", "files")
    assert files["core-instance-registry.json"] == "device-instance-registry"
    assert files["integration-links.json"] == "device-integration-registry"
    assert files["regeneration.json"] == "device-runtime-state"
    assert files["corefs-client-access.json"] == "device-client-grants"
    assert "legacy-draft-cleanup-v1.lock" not in files
