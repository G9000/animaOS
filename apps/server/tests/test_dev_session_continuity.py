from __future__ import annotations

import ast
import asyncio
import base64
import inspect
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, get_ident
from types import SimpleNamespace

import anima_server.services.sessions as sessions_module
import pytest
from anima_server.services.dev_session_snapshot import (
    DEV_SESSION_KEY_ENV,
    DEV_SESSION_STATE_PATH_ENV,
    DevSessionSnapshot,
    DevSessionSnapshotError,
)
from anima_server.services.sessions import UnlockSessionStore
from fastapi import HTTPException


def _payload(*, token: str = "token-one") -> dict[str, object]:
    encoded_key = base64.b64encode(b"k" * 32).decode("ascii")
    return {
        "version": 1,
        "sessions": [
            {
                "token": token,
                "userId": 1,
                "expiresAt": "2099-07-15T10:20:30.123456Z",
                "deks": {"memories": encoded_key},
                "hadCorefsKeys": False,
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
        {
            "version": 1,
            "sessions": [
                {
                    "token": "token",
                    "userId": 1,
                    "expiresAt": "2099-07-15T10:20:30.123456Z",
                    "deks": {"memories": base64.b64encode(b"a" * 32).decode("ascii")},
                    "hadCorefsKeys": "yes",
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


def test_restored_session_reconstructs_runtime_index_from_sqlcipher_key(
    tmp_path: Path,
) -> None:
    snapshot = DevSessionSnapshot(path=tmp_path / "state.bin", key=b"s" * 32)
    snapshot.write(_payload(token="restored-token"))
    sentinel_index = SimpleNamespace()
    calls: list[tuple[object | None, bytes | None]] = []

    def runtime_index_factory(
        corefs_keys: object | None,
        sqlcipher_key: bytes | None,
    ) -> object:
        calls.append((corefs_keys, sqlcipher_key))
        return sentinel_index

    restored = UnlockSessionStore(
        snapshot=snapshot,
        runtime_index_factory=runtime_index_factory,  # type: ignore[arg-type]
    )

    session = restored.resolve("restored-token")
    assert session is not None
    assert session.runtime_index is sentinel_index
    assert calls == [(None, b"k" * 32)]


def test_resolve_repairs_session_created_before_runtime_index_is_ready() -> None:
    from anima_server.services.corefs.indexer import CoreFSProgressiveIndex

    runtime_ready = False
    sentinel_index = CoreFSProgressiveIndex("core-a")
    sentinel_index.unlock(sqlcipher_key=b"q" * 32, local_instance_id="instance-a")
    calls: list[tuple[object | None, bytes | None]] = []

    def runtime_index_factory(
        corefs_keys: object | None,
        sqlcipher_key: bytes | None,
    ) -> object | None:
        calls.append((corefs_keys, sqlcipher_key))
        return sentinel_index if runtime_ready else None

    store = UnlockSessionStore(
        runtime_index_factory=runtime_index_factory,  # type: ignore[arg-type]
    )
    store.set_sqlcipher_key(b"q" * 32)
    token = store.create(7, {"memories": b"m" * 32})

    initial = store.resolve(token)
    assert initial is not None
    assert initial.runtime_index is None

    runtime_ready = True
    repaired = store.resolve(token)

    assert repaired is not None
    assert repaired.runtime_index is sentinel_index
    assert calls[-1] == (None, b"q" * 32)


def test_session_store_does_not_restore_session_that_had_corefs_keys(
    tmp_path: Path,
) -> None:
    snapshot = DevSessionSnapshot(path=tmp_path / "state.bin", key=b"s" * 32)
    store = UnlockSessionStore(snapshot=snapshot)
    store.set_sqlcipher_key(b"q" * 32)
    token = store.create(
        7,
        {"memories": b"m" * 32},
        corefs_keys=object(),
    )

    restored = UnlockSessionStore(snapshot=snapshot)

    assert restored.resolve(token) is None
    assert restored.get_sqlcipher_key() == b"q" * 32
    assert snapshot.load() == {
        "version": 1,
        "sessions": [],
        "sqlcipherKey": base64.b64encode(b"q" * 32).decode("ascii"),
    }


def test_session_store_does_not_restore_legacy_session_without_corefs_marker(
    tmp_path: Path,
) -> None:
    snapshot = DevSessionSnapshot(path=tmp_path / "state.bin", key=b"s" * 32)
    payload = _payload(token="legacy-token")
    sessions = payload["sessions"]
    assert isinstance(sessions, list)
    session = sessions[0]
    assert isinstance(session, dict)
    session.pop("hadCorefsKeys")
    snapshot.write(payload)

    restored = UnlockSessionStore(snapshot=snapshot)

    assert restored.resolve("legacy-token") is None
    assert snapshot.load() == {
        "version": 1,
        "sessions": [],
        "sqlcipherKey": payload["sqlcipherKey"],
    }


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
    assert snapshot.load() == {
        "version": 1,
        "sessions": [],
        "sqlcipherKey": payload["sqlcipherKey"],
    }


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


@pytest.mark.asyncio
async def test_shutdown_preserves_dev_snapshot_for_child_reload(tmp_path: Path) -> None:
    snapshot = DevSessionSnapshot(path=tmp_path / "state.bin", key=b"s" * 32)
    store = UnlockSessionStore(snapshot=snapshot)
    store.set_sqlcipher_key(b"q" * 32)
    token = store.create(3, {"memories": b"m" * 32})
    ciphertext_before_shutdown = snapshot.path.read_bytes()

    await store.shutdown()

    assert snapshot.path.read_bytes() == ciphertext_before_shutdown
    assert store.resolve(token) is None
    restored = UnlockSessionStore(snapshot=snapshot)
    assert restored.resolve(token) is not None
    assert restored.get_sqlcipher_key() == b"q" * 32


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
    environment["ANIMA_DATA_DIR"] = str(tmp_path / "portable" / ".anima")
    environment["ANIMA_RUNTIME_INSTANCE_DATA_DIR"] = str(
        tmp_path / "runtime" / "instance-a"
    )

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


def test_corefs_keys_create_one_server_owned_native_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    class FakeCorefsSession:
        def __init__(self, core_root: str, core_id: str) -> None:
            calls.append((core_root, core_id))

        def begin_close(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        sessions_module,
        "anima_core",
        SimpleNamespace(CorefsSession=FakeCorefsSession),
        raising=False,
    )
    monkeypatch.setattr(
        sessions_module,
        "get_core_dir",
        lambda: tmp_path / "server-core",
        raising=False,
    )
    monkeypatch.setattr(
        sessions_module,
        "get_core_id",
        lambda: "server-core-id",
        raising=False,
    )
    store = UnlockSessionStore()

    first = store.create(31, {"memories": b"a" * 32}, corefs_keys=object())
    second = store.create(32, {"memories": b"b" * 32}, corefs_keys=object())

    first_session = store.resolve(first)
    second_session = store.resolve(second)
    assert first_session is not None
    assert second_session is not None
    assert first_session.corefs_session is not second_session.corefs_session
    assert calls == [
        (str(tmp_path / "server-core"), "server-core-id"),
        (str(tmp_path / "server-core"), "server-core-id"),
    ]


def test_revoking_rotated_token_alias_keeps_shared_session_open() -> None:
    native_sessions: list[SimpleNamespace] = []

    def factory() -> SimpleNamespace:
        native_session = SimpleNamespace(begin_calls=0, close_calls=0)

        def begin_close() -> None:
            native_session.begin_calls += 1

        def close() -> None:
            native_session.close_calls += 1

        native_session.begin_close = begin_close
        native_session.close = close
        native_sessions.append(native_session)
        return native_session

    store = UnlockSessionStore(corefs_session_factory=factory)
    old_token = store.create(31, {"memories": b"a" * 32}, corefs_keys=object())
    new_token = store.replace_user(
        31,
        {"memories": b"b" * 32},
        corefs_keys=object(),
        preserve_existing_tokens=True,
    )
    replacement = store.resolve(new_token)

    assert replacement is not None
    assert store.resolve(old_token) is replacement
    assert native_sessions[0].begin_calls == 1
    assert native_sessions[0].close_calls == 1

    store.revoke(old_token)

    assert store.resolve(new_token) is replacement
    assert native_sessions[1].begin_calls == 0
    assert native_sessions[1].close_calls == 0

    store.revoke(new_token)

    assert native_sessions[1].begin_calls == 1
    assert native_sessions[1].close_calls == 1


def test_replace_user_prepares_replacement_before_publication() -> None:
    order: list[str] = []
    store = UnlockSessionStore()
    old_token = store.create(31, {"memories": b"a" * 32})
    original = store.resolve(old_token)

    def prepare(_session) -> None:
        order.append("prepared")
        assert store.resolve(old_token) is original

    token = store.replace_user(
        31,
        {"memories": b"b" * 32},
        before_publish=prepare,
    )
    order.append("returned")

    assert store.resolve(token) is not None
    assert store.resolve(old_token) is None
    assert order == ["prepared", "returned"]


def test_native_session_without_begin_close_is_rejected_before_publication() -> None:
    class LegacyNativeSession:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    native_session = LegacyNativeSession()
    store = UnlockSessionStore(corefs_session_factory=lambda: native_session)

    with pytest.raises(RuntimeError, match="begin_close"):
        store.create(32, {"memories": b"a" * 32}, corefs_keys=object())

    assert native_session.close_calls == 1
    with store._lock:
        assert store._sessions == {}


def test_unpublished_native_session_is_closed_when_snapshot_commit_fails() -> None:
    snapshot = _FailingSnapshot()
    snapshot.fail_writes = True
    native_session = _BlockingNativeSession()
    native_session.release.set()
    store = UnlockSessionStore(
        snapshot=snapshot,
        corefs_session_factory=lambda: native_session,
    )

    with pytest.raises(OSError, match="snapshot write failed"):
        store.create(33, {"memories": b"a" * 32}, corefs_keys=object())

    assert native_session.close_calls == 1
    assert native_session.returned.is_set()
    assert store.resolve(None) is None


class _BlockingNativeSession:
    def __init__(self, *, close_error: Exception | None = None) -> None:
        self.begin_calls = 0
        self.began = Event()
        self.entered = Event()
        self.release = Event()
        self.returned = Event()
        self.close_calls = 0
        self.close_thread_id: int | None = None
        self.close_error = close_error

    def begin_close(self) -> None:
        self.begin_calls += 1
        self.began.set()

    def close(self) -> None:
        self.close_calls += 1
        self.close_thread_id = get_ident()
        self.entered.set()
        if not self.release.wait(timeout=8):
            raise AssertionError("finite native-close latch was not released")
        try:
            if self.close_error is not None:
                raise self.close_error
        finally:
            self.returned.set()


def _attach_native_session(
    store: UnlockSessionStore,
    token: str,
    native_session: _BlockingNativeSession,
    *,
    expires_at: datetime | None = None,
) -> SimpleNamespace:
    original = store.resolve(token)
    assert original is not None
    attached = SimpleNamespace(
        user_id=original.user_id,
        deks=original.deks,
        expires_at=expires_at or original.expires_at,
        corefs_keys=object(),
        corefs_session=native_session,
    )
    with store._lock:
        store._sessions[token] = attached
        store._rebuild_latest_deks_locked()
    return attached


def _snapshot_tokens(snapshot: _FailingSnapshot) -> set[str]:
    payload = snapshot.load()
    assert payload is not None
    sessions = payload["sessions"]
    assert isinstance(sessions, list)
    return {str(session["token"]) for session in sessions if isinstance(session, dict)}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    ["revoke", "replace_user", "revoke_user", "expiry_purge", "clear"],
)
async def test_native_close_detaches_token_before_extended_wait(
    operation: str,
) -> None:
    snapshot = _FailingSnapshot()
    store = UnlockSessionStore(snapshot=snapshot)
    token = store.create(41, {"memories": b"m" * 32})
    native_session = _BlockingNativeSession()
    attached = _attach_native_session(
        store,
        token,
        native_session,
        expires_at=(
            datetime.now(UTC) - timedelta(seconds=1) if operation == "expiry_purge" else None
        ),
    )

    def close_call() -> object:
        if operation == "revoke":
            return store.revoke(token)
        if operation == "replace_user":
            return store.replace_user(41, {"memories": b"n" * 32})
        if operation == "revoke_user":
            return store.revoke_user(41)
        if operation == "expiry_purge":
            return store.resolve(token)
        return store.clear()

    close_task = asyncio.create_task(asyncio.to_thread(close_call))
    try:
        assert await asyncio.to_thread(native_session.entered.wait, 1), (
            f"{operation} did not start native close"
        )
        with store._lock:
            assert token not in store._sessions
        assert token not in _snapshot_tokens(snapshot)
        assert attached.deks["memories"] == b"m" * 32

        if operation == "revoke":
            await asyncio.sleep(2.1)
            assert not close_task.done()
            assert attached.deks["memories"] == b"m" * 32
    finally:
        native_session.release.set()
        await asyncio.wait_for(close_task, timeout=3)

    assert native_session.close_calls == 1
    assert native_session.returned.is_set()
    assert attached.deks["memories"] == b"\x00" * 32


@pytest.mark.asyncio
async def test_native_close_does_not_block_store_or_async_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.api.routes import auth as auth_route

    store = UnlockSessionStore()
    token = store.create(42, {"memories": b"m" * 32})
    other_token = store.create(43, {"memories": b"n" * 32})
    native_session = _BlockingNativeSession()
    _attach_native_session(store, token, native_session)
    monkeypatch.setattr(auth_route, "unlock_session_store", store)
    monkeypatch.setattr(auth_route, "dispose_all_user_engines", lambda: None)
    request = SimpleNamespace(headers={"x-anima-unlock": token})

    logout_result = auth_route.logout(request)  # type: ignore[arg-type]
    assert inspect.isawaitable(logout_result), "logout must await off-loop native close"
    logout_task = asyncio.create_task(logout_result)
    try:
        assert await asyncio.to_thread(native_session.entered.wait, 1)
        assert store.resolve(token) is None

        heartbeat_count = 0

        async def heartbeat() -> None:
            nonlocal heartbeat_count
            for _ in range(10):
                await asyncio.sleep(0.01)
                heartbeat_count += 1

        heartbeat_task = asyncio.create_task(heartbeat())
        resolved = await asyncio.wait_for(
            asyncio.to_thread(store.resolve, other_token),
            timeout=1,
        )
        await asyncio.wait_for(heartbeat_task, timeout=1)

        assert resolved is not None
        assert heartbeat_count == 10
        assert native_session.close_thread_id != get_ident()
        assert not logout_task.done()
    finally:
        native_session.release.set()
        await asyncio.wait_for(logout_task, timeout=3)

    assert native_session.close_calls == 1


@pytest.mark.asyncio
async def test_logout_rotation_alias_keeps_sqlcipher_key_and_user_engines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.api.routes import auth as auth_route

    store = UnlockSessionStore()
    old_token = store.create(44, {"memories": b"m" * 32})
    replacement_token = store.replace_user(
        44,
        {"memories": b"n" * 32},
        preserve_existing_tokens=True,
    )
    store.set_sqlcipher_key(b"sqlcipher-key")
    dispose_calls: list[bool] = []
    monkeypatch.setattr(auth_route, "unlock_session_store", store)
    monkeypatch.setattr(
        auth_route,
        "dispose_all_user_engines",
        lambda: dispose_calls.append(True),
    )

    response = await auth_route.logout(  # type: ignore[arg-type]
        SimpleNamespace(headers={"x-anima-unlock": old_token})
    )

    assert response == {"success": True}
    assert store.resolve(old_token) is None
    assert store.resolve(replacement_token) is not None
    assert store.get_sqlcipher_key() == b"sqlcipher-key"
    assert dispose_calls == []


@pytest.mark.asyncio
async def test_shutdown_waits_for_each_native_close_result() -> None:
    store = UnlockSessionStore()
    first_token = store.create(51, {"memories": b"a" * 32})
    second_token = store.create(52, {"memories": b"b" * 32})
    first_native = _BlockingNativeSession()
    second_native = _BlockingNativeSession()
    first_session = _attach_native_session(store, first_token, first_native)
    second_session = _attach_native_session(store, second_token, second_native)

    shutdown_task = asyncio.create_task(store.shutdown())
    try:
        assert await asyncio.to_thread(first_native.began.wait, 1)
        assert await asyncio.to_thread(second_native.began.wait, 1)
        assert await asyncio.to_thread(first_native.entered.wait, 1)
        assert not second_native.entered.is_set()
        assert store.resolve(first_token) is None
        assert store.resolve(second_token) is None
        await asyncio.sleep(2.1)
        assert not shutdown_task.done()
        assert first_session.deks["memories"] == b"a" * 32
        assert second_session.deks["memories"] == b"b" * 32
    finally:
        first_native.release.set()
        second_native.release.set()
        await asyncio.wait_for(shutdown_task, timeout=3)

    assert first_native.close_calls == 1
    assert second_native.close_calls == 1
    assert first_session.deks["memories"] == b"\x00" * 32
    assert second_session.deks["memories"] == b"\x00" * 32


@pytest.mark.asyncio
async def test_shutdown_logs_actual_close_error_and_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = UnlockSessionStore()
    first_token = store.create(61, {"memories": b"a" * 32})
    second_token = store.create(62, {"memories": b"b" * 32})
    first_native = _BlockingNativeSession(close_error=RuntimeError("close failed"))
    second_native = _BlockingNativeSession()
    _attach_native_session(store, first_token, first_native)
    _attach_native_session(store, second_token, second_native)
    second_native.release.set()

    with caplog.at_level("ERROR", logger="anima_server.services.sessions"):
        shutdown_task = asyncio.create_task(store.shutdown())
        try:
            assert await asyncio.to_thread(first_native.entered.wait, 1)
            assert "close failed" not in caplog.text
            assert store.resolve(first_token) is None
            assert store.resolve(second_token) is None
        finally:
            first_native.release.set()
            await asyncio.wait_for(shutdown_task, timeout=3)

    assert first_native.close_calls == 1
    assert second_native.close_calls == 1
    assert "close failed" in caplog.text


@pytest.mark.asyncio
async def test_shutdown_waits_for_an_existing_inflight_close_without_reclosing() -> None:
    store = UnlockSessionStore()
    token = store.create(71, {"memories": b"a" * 32})
    native_session = _BlockingNativeSession()
    attached = _attach_native_session(store, token, native_session)

    revoke_task = asyncio.create_task(asyncio.to_thread(store.revoke, token))
    try:
        assert await asyncio.to_thread(native_session.entered.wait, 1)
        shutdown_task = asyncio.create_task(store.shutdown())
        await asyncio.sleep(0.1)
        assert not shutdown_task.done()
        assert native_session.close_calls == 1
        assert attached.deks["memories"] == b"a" * 32
    finally:
        native_session.release.set()
        await asyncio.wait_for(revoke_task, timeout=3)

    await asyncio.wait_for(shutdown_task, timeout=3)
    assert native_session.close_calls == 1
    assert attached.deks["memories"] == b"\x00" * 32


@pytest.mark.asyncio
async def test_shutdown_is_terminal_against_session_resurrection() -> None:
    store = UnlockSessionStore()

    await store.shutdown()

    with pytest.raises(RuntimeError, match="shut down"):
        store.create(72, {"memories": b"a" * 32})
    assert store.resolve(None) is None


@pytest.mark.asyncio
async def test_cancelled_logout_still_finishes_native_close_and_zeroes_deks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.api.routes import auth as auth_route

    store = UnlockSessionStore()
    token = store.create(73, {"memories": b"a" * 32})
    native_session = _BlockingNativeSession()
    attached = _attach_native_session(store, token, native_session)
    monkeypatch.setattr(auth_route, "unlock_session_store", store)
    monkeypatch.setattr(auth_route, "dispose_all_user_engines", lambda: None)
    request = SimpleNamespace(headers={"x-anima-unlock": token})

    logout_task = asyncio.create_task(auth_route.logout(request))  # type: ignore[arg-type]
    assert await asyncio.to_thread(native_session.entered.wait, 1)
    logout_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await logout_task

    native_session.release.set()
    assert await asyncio.to_thread(native_session.returned.wait, 3)
    assert attached.deks["memories"] == b"\x00" * 32
    assert native_session.close_calls == 1


@pytest.mark.asyncio
async def test_async_unlock_dependency_offloads_extended_expiry_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from anima_server.api.deps import unlock as unlock_deps

    store = UnlockSessionStore()
    token = store.create(74, {"memories": b"a" * 32})
    native_session = _BlockingNativeSession()
    _attach_native_session(
        store,
        token,
        native_session,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    monkeypatch.setattr(unlock_deps, "unlock_session_store", store)
    request = SimpleNamespace(headers={"x-anima-unlock": token})

    resolution = asyncio.create_task(
        unlock_deps.require_unlocked_session_async(request)  # type: ignore[arg-type]
    )
    try:
        assert await asyncio.to_thread(native_session.entered.wait, 1)
        heartbeat = 0
        for _ in range(10):
            await asyncio.sleep(0.01)
            heartbeat += 1
        assert heartbeat == 10
        assert not resolution.done()
        with store._lock:
            assert token not in store._sessions
    finally:
        native_session.release.set()

    with pytest.raises(HTTPException) as exc_info:
        await asyncio.wait_for(resolution, timeout=3)
    assert exc_info.value.status_code == 401
    assert native_session.close_calls == 1


def test_async_code_does_not_call_sync_unlock_accessors() -> None:
    source_dir = Path(__file__).parents[1] / "src" / "anima_server"
    forbidden_calls: list[str] = []

    class DirectAsyncCallVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.calls: list[ast.Call] = []

        def visit_Call(self, node: ast.Call) -> None:
            self.calls.append(node)
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            del node

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            del node

        def visit_Lambda(self, node: ast.Lambda) -> None:
            del node

    for path in sorted(source_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, ast.AsyncFunctionDef):
                continue
            visitor = DirectAsyncCallVisitor()
            for statement in function.body:
                visitor.visit(statement)
            for node in visitor.calls:
                if isinstance(node.func, ast.Name) and node.func.id in {
                    "get_active_dek",
                    "get_active_deks",
                    "require_unlocked_session",
                    "require_unlocked_user",
                }:
                    forbidden_calls.append(
                        f"{path.relative_to(source_dir)}:"
                        f"{node.lineno}:{function.name}:{node.func.id}"
                    )

    assert forbidden_calls == []


@pytest.mark.asyncio
async def test_shutdown_drains_inflight_native_construction_before_return() -> None:
    factory_entered = Event()
    factory_release = Event()
    native_session = _BlockingNativeSession()
    native_session.release.set()

    def factory() -> _BlockingNativeSession:
        factory_entered.set()
        if not factory_release.wait(timeout=8):
            raise AssertionError("finite construction latch was not released")
        return native_session

    store = UnlockSessionStore(corefs_session_factory=factory)
    create_task = asyncio.create_task(
        asyncio.to_thread(
            store.create,
            75,
            {"memories": b"a" * 32},
            corefs_keys=object(),
        )
    )
    assert await asyncio.to_thread(factory_entered.wait, 1)
    shutdown_task = asyncio.create_task(store.shutdown())
    try:
        await asyncio.sleep(0.1)
        assert not shutdown_task.done()
        with pytest.raises(RuntimeError, match="teardown is active"):
            store.start()
    finally:
        factory_release.set()

    with pytest.raises(RuntimeError, match="shut down"):
        await asyncio.wait_for(create_task, timeout=3)
    await asyncio.wait_for(shutdown_task, timeout=3)
    assert native_session.close_calls == 1


@pytest.mark.asyncio
async def test_cancelled_async_creation_revokes_unclaimed_session() -> None:
    factory_entered = Event()
    factory_release = Event()
    factory_completed = Event()
    native_session = _BlockingNativeSession()
    native_session.release.set()

    def factory() -> _BlockingNativeSession:
        factory_entered.set()
        if not factory_release.wait(timeout=8):
            raise AssertionError("finite construction latch was not released")
        factory_completed.set()
        return native_session

    store = UnlockSessionStore(corefs_session_factory=factory)
    create_task = asyncio.create_task(
        store.create_async(
            77,
            {"memories": b"a" * 32},
            corefs_keys=object(),
        )
    )
    assert await asyncio.to_thread(factory_entered.wait, 1)

    try:
        create_task.cancel()
        await asyncio.sleep(0.1)
        assert not create_task.done()
    finally:
        factory_release.set()
        assert await asyncio.to_thread(factory_completed.wait, 1)

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(create_task, timeout=3)
    with store._lock:
        assert store._sessions == {}
    assert native_session.close_calls == 1


@pytest.mark.asyncio
async def test_cancelled_shutdown_defers_cancellation_until_native_close_finishes() -> None:
    store = UnlockSessionStore()
    token = store.create(76, {"memories": b"a" * 32})
    native_session = _BlockingNativeSession()
    attached = _attach_native_session(store, token, native_session)

    shutdown_task = asyncio.create_task(store.shutdown())
    assert await asyncio.to_thread(native_session.entered.wait, 1)
    shutdown_task.cancel()
    try:
        await asyncio.sleep(0.1)
        assert not shutdown_task.done()
        assert attached.deks["memories"] == b"a" * 32
    finally:
        native_session.release.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(shutdown_task, timeout=3)
    assert attached.deks["memories"] == b"\x00" * 32
    assert native_session.close_calls == 1


@pytest.mark.asyncio
async def test_shutdown_rejects_sqlcipher_key_resurrection() -> None:
    store = UnlockSessionStore()

    await store.shutdown()

    with pytest.raises(RuntimeError, match="shut down"):
        store.set_sqlcipher_key(b"q" * 32)
    assert store.get_sqlcipher_key() is None
