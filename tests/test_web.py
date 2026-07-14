from fastapi.testclient import TestClient

from seo_ops.web.app import create_app


def test_health_and_dashboard_render(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        health = client.get("/api/health")
        dashboard = client.get("/")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert dashboard.status_code == 200
    assert "今日工作台" in dashboard.text
