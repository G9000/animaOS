from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from anima_server.services.dev_session_snapshot import (
    DEV_SESSION_KEY_ENV,
    DEV_SESSION_STATE_PATH_ENV,
    DevSessionSnapshot,
    DevSessionSnapshotError,
)
from anima_server.services.sessions import UnlockSessionStore


def _payload(*, token: str = "token-one") -> dict[str, object]:
    encoded_key = base64.b64encode(b"k" * 32).decode("ascii")
    return {
        "version": 1,
        "sessions": [
            {
                "token": token,
                "userId": 1,
                "expiresAt": "2026-07-15T10:20:30.123456Z",
                "deks": {"memories": encoded_key},
            }
        ],
        "sqlcipherKey": encoded_key,
    }


def test_snapshot_round_trip_is_encrypted(tmp_path: Path) -> None:
    snapshot = DevSessionSnapshot(path=tmp_path / "state.bin", key=b"s" * 32)

    snapshot.write(_payload())

    ciphertext = snapshot.path.read_bytes()
    assert b"token-one" not in ciphertext
    assert b'"sessions"' not in ciphertext
    assert snapshot.load() == _payload()


def test_snapshot_rejects_tampering_and_wrong_key(tmp_path: Path) -> None:
    snapshot = DevSessionSnapshot(path=tmp_path / "state.bin", key=b"s" * 32)
    snapshot.write(_payload())
    tampered = bytearray(snapshot.path.read_bytes())
    tampered[-1] ^= 1
    snapshot.path.write_bytes(tampered)

    with pytest.raises(DevSessionSnapshotError):
        snapshot.load()

    snapshot.write(_payload())
    wrong_key = DevSessionSnapshot(path=snapshot.path, key=b"w" * 32)
    with pytest.raises(DevSessionSnapshotError):
        wrong_key.load()


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 2, "sessions": [], "sqlcipherKey": None},
        {"version": 1, "sessions": "bad", "sqlcipherKey": None},
        {
            "version": 1,
            "sessions": [
                {
                    "token": "duplicate",
                    "userId": 1,
                    "expiresAt": "2026-07-15T10:20:30Z",
                    "deks": {"memories": base64.b64encode(b"a" * 32).decode("ascii")},
                },
                {
                    "token": "duplicate",
                    "userId": 2,
                    "expiresAt": "2026-07-15T10:20:30Z",
                    "deks": {"memories": base64.b64encode(b"b" * 32).decode("ascii")},
                },
            ],
            "sqlcipherKey": None,
        },
        {
            "version": 1,
            "sessions": [
                {
                    "token": "token",
                    "userId": 1,
                    "expiresAt": "2026-07-15T10:20:30",
                    "deks": {"memories": base64.b64encode(b"short").decode("ascii")},
                }
            ],
            "sqlcipherKey": None,
        },
    ],
)
def test_snapshot_rejects_invalid_payloads(tmp_path: Path, payload: dict[str, object]) -> None:
    snapshot = DevSessionSnapshot(path=tmp_path / "state.bin", key=b"s" * 32)

    with pytest.raises(DevSessionSnapshotError):
        snapshot.write(payload)


def test_snapshot_environment_requires_both_valid_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DEV_SESSION_STATE_PATH_ENV, raising=False)
    monkeypatch.delenv(DEV_SESSION_KEY_ENV, raising=False)
    assert DevSessionSnapshot.from_environment() is None

    monkeypatch.setenv(DEV_SESSION_STATE_PATH_ENV, str(tmp_path / "state.bin"))
    assert DevSessionSnapshot.from_environment() is None

    monkeypatch.setenv(DEV_SESSION_KEY_ENV, "not-base64")
    assert DevSessionSnapshot.from_environment() is None

    monkeypatch.setenv(DEV_SESSION_KEY_ENV, base64.b64encode(b"s" * 32).decode("ascii"))
    snapshot = DevSessionSnapshot.from_environment()
    assert snapshot is not None
    assert snapshot.path == tmp_path / "state.bin"


def test_failed_atomic_replace_preserves_previous_ciphertext(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = DevSessionSnapshot(path=tmp_path / "state.bin", key=b"s" * 32)
    snapshot.write(_payload(token="old-token"))
    previous = snapshot.path.read_bytes()

    def _fail_replace(_source: object, _target: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("anima_server.services.dev_session_snapshot.os.replace", _fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        snapshot.write(_payload(token="new-token"))

    assert snapshot.path.read_bytes() == previous
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_session_store_restores_unlock_and_sqlcipher_without_db_viewer_state(
    tmp_path: Path,
) -> None:
    snapshot = DevSessionSnapshot(path=tmp_path / "state.bin", key=b"s" * 32)
    store = UnlockSessionStore(snapshot=snapshot)
    store.set_sqlcipher_key(b"q" * 32)
    token = store.create(7, {"memories": b"m" * 32, "files": b"f" * 32})
    store.set_db_viewer_verified_at(token, 123.0)

    restored = UnlockSessionStore(snapshot=snapshot)

    session = restored.resolve(token)
    assert session is not None
    assert session.user_id == 7
    assert session.deks == {"memories": b"m" * 32, "files": b"f" * 32}
    assert restored.get_sqlcipher_key() == b"q" * 32
    assert restored.get_db_viewer_verified_at(token) is None


def test_session_store_discards_expired_snapshot_sessions(tmp_path: Path) -> None:
    snapshot = DevSessionSnapshot(path=tmp_path / "state.bin", key=b"s" * 32)
    payload = _payload(token="expired-token")
    sessions = payload["sessions"]
    assert isinstance(sessions, list)
    session = sessions[0]
    assert isinstance(session, dict)
    session["expiresAt"] = "2020-01-01T00:00:00.000000Z"
    snapshot.write(payload)

    restored = UnlockSessionStore(snapshot=snapshot)

    assert restored.resolve("expired-token") is None
    assert snapshot.load() == {"version": 1, "sessions": [], "sqlcipherKey": payload["sqlcipherKey"]}


def test_session_store_persists_revocation_and_clear(tmp_path: Path) -> None:
    snapshot = DevSessionSnapshot(path=tmp_path / "state.bin", key=b"s" * 32)
    store = UnlockSessionStore(snapshot=snapshot)
    store.set_sqlcipher_key(b"q" * 32)
    first = store.create(1, {"memories": b"a" * 32})
    second = store.create(2, {"memories": b"b" * 32})

    store.revoke(first)
    after_revoke = UnlockSessionStore(snapshot=snapshot)
    assert after_revoke.resolve(first) is None
    assert after_revoke.resolve(second) is not None

    after_revoke.revoke_user(2)
    after_user_revoke = UnlockSessionStore(snapshot=snapshot)
    assert after_user_revoke.resolve(second) is None

    third = after_user_revoke.create(3, {"memories": b"c" * 32})
    after_user_revoke.clear_sqlcipher_key()
    after_key_clear = UnlockSessionStore(snapshot=snapshot)
    assert after_key_clear.resolve(third) is not None
    assert after_key_clear.get_sqlcipher_key() is None

    after_key_clear.clear()
    after_clear = UnlockSessionStore(snapshot=snapshot)
    assert after_clear.resolve(third) is None
    assert after_clear.get_sqlcipher_key() is None


class _FailingSnapshot:
    def __init__(self) -> None:
        self.payload: dict[str, object] | None = None
        self.fail_writes = False

    def load(self) -> dict[str, object] | None:
        if self.payload is None:
            return None
        return json.loads(json.dumps(self.payload))

    def write(self, payload: dict[str, object]) -> None:
        if self.fail_writes:
            raise OSError("snapshot write failed")
        self.payload = json.loads(json.dumps(payload))


@pytest.mark.parametrize("operation", ["revoke", "revoke_user", "clear", "clear_key"])
def test_failed_security_reduction_keeps_previous_memory_and_snapshot(
    operation: str,
) -> None:
    snapshot = _FailingSnapshot()
    store = UnlockSessionStore(snapshot=snapshot)
    store.set_sqlcipher_key(b"q" * 32)
    token = store.create(4, {"memories": b"m" * 32})
    previous_payload = snapshot.load()
    snapshot.fail_writes = True

    with pytest.raises(OSError, match="snapshot write failed"):
        if operation == "revoke":
            store.revoke(token)
        elif operation == "revoke_user":
            store.revoke_user(4)
        elif operation == "clear":
            store.clear()
        else:
            store.clear_sqlcipher_key()

    assert store.resolve(token) is not None
    assert store.get_sqlcipher_key() == b"q" * 32
    assert snapshot.load() == previous_payload


def test_concurrent_session_and_key_mutations_restore_coherently() -> None:
    snapshot = _FailingSnapshot()
    store = UnlockSessionStore(snapshot=snapshot)

    def _create(index: int) -> str:
        store.set_sqlcipher_key(bytes([index + 1]) * 32)
        return store.create(index, {"memories": bytes([index + 11]) * 32})

    with ThreadPoolExecutor(max_workers=6) as executor:
        tokens = list(executor.map(_create, range(10)))

    restored = UnlockSessionStore(snapshot=snapshot)
    assert all(restored.resolve(token) is not None for token in tokens)
    assert restored.get_sqlcipher_key() in {bytes([index + 1]) * 32 for index in range(10)}


def test_restore_preserves_latest_session_deks_for_each_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import anima_server.services.sessions as sessions_module

    tokens = iter(["zzz-older-token", "aaa-newer-token"])
    monkeypatch.setattr(sessions_module.secrets, "token_urlsafe", lambda _size: next(tokens))
    snapshot = DevSessionSnapshot(path=tmp_path / "state.bin", key=b"s" * 32)
    store = UnlockSessionStore(snapshot=snapshot)

    store.create(9, {"memories": b"o" * 32})
    store.create(9, {"memories": b"n" * 32})

    assert store.get_active_dek(9) == b"n" * 32
    assert UnlockSessionStore(snapshot=snapshot).get_active_dek(9) == b"n" * 32


def test_session_store_without_snapshot_remains_process_local() -> None:
    store = UnlockSessionStore()
    token = store.create(1, {"memories": b"m" * 32})

    assert store.resolve(token) is not None
    assert UnlockSessionStore().resolve(token) is None


def test_global_store_restores_snapshot_during_module_import(tmp_path: Path) -> None:
    snapshot = DevSessionSnapshot(path=tmp_path / "state.bin", key=b"s" * 32)
    snapshot.write(_payload(token="import-token"))
    environment = os.environ.copy()
    environment[DEV_SESSION_STATE_PATH_ENV] = str(snapshot.path)
    environment[DEV_SESSION_KEY_ENV] = base64.b64encode(b"s" * 32).decode("ascii")

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from anima_server.services.sessions import unlock_session_store; "
                "assert unlock_session_store.resolve('import-token') is not None"
            ),
        ],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
