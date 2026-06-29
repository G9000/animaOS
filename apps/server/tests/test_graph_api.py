from __future__ import annotations

from datetime import UTC, datetime

from anima_server.db.session import get_user_session_factory
from anima_server.models import KGEntity, KGRelation
from conftest import managed_test_client
from fastapi.testclient import TestClient


def _register_user(
    client: TestClient,
    *,
    username: str = "graph-user",
    password: str = "pw123456",
    name: str = "Graph User",
) -> dict[str, object]:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "name": name},
    )
    assert response.status_code == 201
    return response.json()


def _headers(payload: dict[str, object]) -> dict[str, str]:
    return {"x-anima-unlock": str(payload["unlockToken"])}


def test_graph_current_endpoints_filter_superseded_relations() -> None:
    with managed_test_client("anima-graph-api-") as client:
        payload = _register_user(client)
        headers = _headers(payload)
        user_id = int(payload["id"])
        session_factory = get_user_session_factory(user_id)

        with session_factory() as db:
            user = KGEntity(
                user_id=user_id,
                name="User",
                name_normalized="user",
                entity_type="person",
            )
            acme = KGEntity(
                user_id=user_id,
                name="Acme",
                name_normalized="acme",
                entity_type="organization",
            )
            anthropic = KGEntity(
                user_id=user_id,
                name="Anthropic",
                name_normalized="anthropic",
                entity_type="organization",
            )
            db.add_all([user, acme, anthropic])
            db.flush()
            user_entity_id = user.id

            db.add_all(
                [
                    KGRelation(
                        user_id=user_id,
                        source_id=user.id,
                        destination_id=acme.id,
                        relation_type="works_at",
                        mentions=2,
                        status="superseded",
                        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
                        valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                        valid_to=datetime(2026, 6, 1, tzinfo=UTC),
                    ),
                    KGRelation(
                        user_id=user_id,
                        source_id=user.id,
                        destination_id=anthropic.id,
                        relation_type="works_at",
                        mentions=1,
                        status="active",
                        observed_at=datetime(2026, 6, 1, tzinfo=UTC),
                        valid_from=datetime(2026, 6, 1, tzinfo=UTC),
                    ),
                ]
            )
            db.commit()

        entity_response = client.get(
            f"/api/graph/{user_id}/entities/{user_entity_id}",
            headers=headers,
        )
        assert entity_response.status_code == 200
        outgoing = entity_response.json()["outgoingRelations"]
        assert [(relation["type"], relation["target"]["name"]) for relation in outgoing] == [
            ("works_at", "Anthropic")
        ]

        relations_response = client.get(f"/api/graph/{user_id}/relations", headers=headers)
        assert relations_response.status_code == 200
        relations = relations_response.json()["relations"]
        assert [(relation["type"], relation["target"]["name"]) for relation in relations] == [
            ("works_at", "Anthropic")
        ]

        overview_response = client.get(f"/api/graph/{user_id}/overview", headers=headers)
        assert overview_response.status_code == 200
        overview = overview_response.json()
        assert overview["relationCount"] == 1
        assert overview["relationTypeDistribution"] == {"works_at": 1}
