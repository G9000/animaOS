from conftest import managed_test_client


def test_health() -> None:
    with managed_test_client("anima-health-test-", invalidate_agent=False) as client:
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "service": "server",
            "environment": "development",
        }


def test_api_health() -> None:
    with managed_test_client("anima-health-test-", invalidate_agent=False) as client:
        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "service": "server",
            "environment": "development",
        }
