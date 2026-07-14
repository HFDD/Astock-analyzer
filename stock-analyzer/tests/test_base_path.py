import importlib
import sys
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_mount_root_normalizes_and_neighboring_prefix_stays_404():
    main = importlib.import_module("src.main")
    with mock.patch.object(main, "init_db", return_value=None), TestClient(main.app) as client:
        redirect = client.get("/a-stock", follow_redirects=False)
        mounted = client.get("/a-stock/")
        missing = client.get("/a-stock/not-found")
        neighbor = client.get("/a-stock-old")

    assert redirect.status_code == 308
    assert redirect.headers["location"] == "/a-stock/"
    assert mounted.status_code == 200
    assert "A股智能分析" in mounted.text
    assert missing.status_code == 404
    assert neighbor.status_code == 404


def test_mounted_api_keeps_auth_route_available():
    main = importlib.import_module("src.main")
    login_result = {
        "access_token": "test-token",
        "token_type": "bearer",
        "user": {"username": "demo"},
    }
    with (
        mock.patch.object(main, "init_db", return_value=None),
        mock.patch.object(main, "login_user", return_value=login_result),
        TestClient(main.app) as client,
    ):
        response = client.post(
            "/a-stock/api/auth/login",
            json={"username": "demo", "password": "demo123456"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["access_token"] == "test-token"


def test_frontend_builds_api_urls_from_mount_path():
    html = (ROOT / "src" / "templates" / "index.html").read_text(encoding="utf-8")
    assert "window.location.pathname === '/a-stock'" in html
    assert "window.location.pathname.startsWith('/a-stock/')" in html
    assert "const API_BASE = `${window.location.origin}${APP_BASE_PATH}`;" in html


def test_proxy_secret_blocks_direct_origin_access_but_keeps_health_public():
    main = importlib.import_module("src.main")
    inner = FastAPI()

    @inner.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @inner.get("/api/private")
    def private():
        return {"data": "ok"}

    protected = main.MountedProxyMiddleware(inner, "/a-stock", "edge-secret")
    with TestClient(protected) as client:
        health = client.get("/a-stock/healthz")
        forbidden = client.get("/a-stock/api/private")
        allowed = client.get(
            "/a-stock/api/private",
            headers={"X-A-Stock-Proxy-Secret": "edge-secret"},
        )

    assert health.status_code == 200
    assert forbidden.status_code == 403
    assert allowed.status_code == 200
