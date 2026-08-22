from __future__ import annotations

import json

from anima_server import config as config_module
from anima_server.api.routes import credentials as credentials_routes
from anima_server.config import settings
from anima_server.services.credentials import (
    CredentialCapabilityBroker,
    CredentialCapabilityError,
    CredentialStore,
    MemoryCredentialBackend,
    broker_bootstrap_reference,
    credential_reference,
    credential_store,
    provision_broker_bootstrap_secret,
    set_credential_store_for_tests,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_credential_references_are_stable_non_reversible_and_scoped() -> None:
    first = credential_reference("provider", "openai")
    assert first == credential_reference("provider", "openai")
    assert first != credential_reference("provider", "anthropic")
    assert "openai" not in first
    assert len(first) == len("anima-credential:v1:") + 64


def test_credential_store_copy_verifies_and_deletes() -> None:
    backend = MemoryCredentialBackend()
    store = CredentialStore(backend)
    reference = credential_reference("provider", "openai")

    store.put(reference, "sk-private")
    assert store.get(reference) == "sk-private"
    store.delete(reference)
    assert store.get(reference) is None


def test_launcher_bootstrap_rotates_the_previous_process_pair_secret(
    request,
) -> None:
    previous_store = credential_store()
    request.addfinalizer(lambda: set_credential_store_for_tests(previous_store))
    store = CredentialStore(MemoryCredentialBackend())
    set_credential_store_for_tests(store)

    assert provision_broker_bootstrap_secret("first-process-pair") == "first-process-pair"
    assert provision_broker_bootstrap_secret("second-process-pair") == "second-process-pair"
    assert store.get(broker_bootstrap_reference()) == "second-process-pair"


def test_capabilities_are_short_lived_audience_bound_and_one_shot() -> None:
    backend = MemoryCredentialBackend()
    store = CredentialStore(backend)
    reference = credential_reference("mod.google", "oauth:7")
    store.put(reference, "private-token")
    broker = CredentialCapabilityBroker()
    capability = broker.issue(
        audience="anima-mod:google",
        user_id=7,
        references=[reference],
        ttl_seconds=5,
    )

    try:
        broker.consume(
            token=capability.token,
            audience="anima-mod:discord",
            user_id=7,
            store=store,
        )
    except CredentialCapabilityError:
        pass
    else:  # pragma: no cover - fail-closed assertion
        raise AssertionError("wrong-audience capability was accepted")

    replay = broker.issue(
        audience="anima-mod:google",
        user_id=7,
        references=[reference],
        ttl_seconds=5,
    )
    assert broker.consume(
        token=replay.token,
        audience="anima-mod:google",
        user_id=7,
        store=store,
    ) == {reference: "private-token"}
    try:
        broker.consume(
            token=replay.token,
            audience="anima-mod:google",
            user_id=7,
            store=store,
        )
    except CredentialCapabilityError:
        pass
    else:  # pragma: no cover - fail-closed assertion
        raise AssertionError("one-shot credential capability replayed")

    machine = broker.issue(
        audience="anima-mod:google",
        user_id=0,
        references=[reference],
    )
    assert broker.consume(
        token=machine.token,
        audience="anima-mod:google",
        user_id=0,
        store=store,
    ) == {reference: "private-token"}


def test_broker_routes_require_loopback_auth_unlock_and_do_not_offer_generic_get(
    request,
    monkeypatch,
) -> None:
    previous_store = credential_store()
    request.addfinalizer(lambda: set_credential_store_for_tests(previous_store))
    backend = MemoryCredentialBackend()
    store = CredentialStore(backend)
    set_credential_store_for_tests(store)
    credentials_routes.credential_capability_broker.revoke_all()
    bootstrap = "broker-secret"
    store.put(broker_bootstrap_reference(), bootstrap)
    reference = credential_reference("mod.google", "oauth:7")
    store.put(reference, "oauth-secret")
    monkeypatch.setattr(credentials_routes, "_is_loopback_request", lambda _request: True)
    monkeypatch.setattr(credentials_routes, "_is_user_unlocked", lambda user_id: user_id == 7)
    app = FastAPI()
    app.include_router(credentials_routes.router)
    client = TestClient(app)

    assert client.get("/api/credentials").status_code == 404
    invalid_audience = client.post(
        "/api/credentials/capabilities",
        headers={"x-anima-credential-broker": bootstrap},
        json={
            "audience": "anima-mod:google/../../browser",
            "userId": 7,
            "references": [reference],
        },
    )
    assert invalid_audience.status_code == 400
    issue = client.post(
        "/api/credentials/capabilities",
        headers={"x-anima-credential-broker": bootstrap},
        json={
            "audience": "anima-mod:google",
            "userId": 7,
            "references": [reference],
            "ttlSeconds": 5,
        },
    )
    assert issue.status_code == 200
    capability = issue.json()["capability"]

    unauthenticated = client.post(
        "/api/credentials/redeem",
        json={
            "audience": "anima-mod:google",
            "userId": 7,
            "capability": capability,
        },
    )
    assert unauthenticated.status_code == 401

    redeemed = client.post(
        "/api/credentials/redeem",
        headers={"x-anima-credential-broker": bootstrap},
        json={
            "audience": "anima-mod:google",
            "userId": 7,
            "capability": capability,
        },
    )
    assert redeemed.status_code == 200
    assert redeemed.json() == {"secrets": {reference: "oauth-secret"}}

    replay = client.post(
        "/api/credentials/redeem",
        headers={"x-anima-credential-broker": bootstrap},
        json={
            "audience": "anima-mod:google",
            "userId": 7,
            "capability": capability,
        },
    )
    assert replay.status_code == 401

    new_reference = credential_reference("mod.google", "client-secret")
    stored = client.post(
        "/api/credentials/secrets",
        headers={"x-anima-credential-broker": bootstrap},
        json={
            "audience": "anima-mod:google",
            "reference": new_reference,
            "secret": "private-client-secret",
        },
    )
    assert stored.status_code == 204
    assert store.get(new_reference) == "private-client-secret"
    deleted = client.request(
        "DELETE",
        "/api/credentials/secrets",
        headers={"x-anima-credential-broker": bootstrap},
        json={
            "audience": "anima-mod:google",
            "reference": new_reference,
        },
    )
    assert deleted.status_code == 204
    assert store.get(new_reference) is None

    locked = client.post(
        "/api/credentials/capabilities",
        headers={"x-anima-credential-broker": bootstrap},
        json={
            "audience": "anima-mod:google",
            "userId": 8,
            "references": [reference],
        },
    )
    assert locked.status_code == 423


def test_runtime_config_copy_verifies_secrets_into_os_credentials_and_scrubs_file(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "runtime-config.json"
    monkeypatch.setattr(config_module, "get_runtime_settings_path", lambda: path)
    previous = {
        field: getattr(settings, field) for field in config_module._PERSISTED_RUNTIME_SETTING_FIELDS
    }
    path.write_text(
        json.dumps(
            {
                "agent_provider": "openai",
                "agent_model": "gpt-4o-mini",
                "agent_api_key": "legacy-flat-secret",
                "agent_api_keys_json": json.dumps({"openai": "legacy-provider-secret"}),
                "agent_embedding_provider": "openai",
                "agent_embedding_api_key": "legacy-embedding-secret",
            }
        ),
        encoding="utf-8",
    )
    try:
        config_module.load_persisted_runtime_settings()
        assert settings.agent_api_key == "legacy-flat-secret"
        assert config_module.get_provider_api_key("openai") == "legacy-provider-secret"
        assert settings.agent_embedding_api_key == "legacy-embedding-secret"

        scrubbed = path.read_text(encoding="utf-8")
        assert "legacy-flat-secret" not in scrubbed
        assert "legacy-provider-secret" not in scrubbed
        assert "legacy-embedding-secret" not in scrubbed
        payload = json.loads(scrubbed)
        assert payload["agent_api_key"].startswith("anima-credential:v1:")
        assert json.loads(payload["agent_api_keys_json"])["openai"].startswith(
            "anima-credential:v1:"
        )

        settings.agent_api_key = ""
        settings.agent_api_keys_json = "{}"
        settings.agent_embedding_api_key = ""
        config_module.load_persisted_runtime_settings()
        assert settings.agent_api_key == "legacy-flat-secret"
        assert config_module.get_provider_api_key("openai") == "legacy-provider-secret"
        assert settings.agent_embedding_api_key == "legacy-embedding-secret"
    finally:
        for field, value in previous.items():
            setattr(settings, field, value)
