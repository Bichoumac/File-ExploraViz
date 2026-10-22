from __future__ import annotations

import dataclasses
import os

import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import wait_until


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        ctx = app.state.ctx
        wait_until(ctx.state.index_ready.is_set)
        yield c


def login(c: TestClient, password: str = "motdepasse-test") -> None:
    r = c.post("/api/auth/login", json={"username": "admin", "password": password})
    assert r.status_code == 200, r.text


def scan_and_wait(c: TestClient, root: str | None = None) -> None:
    r = c.post("/api/scan", json={"root": root})
    assert r.status_code == 202, r.text
    ctx = c.app.state.ctx
    wait_until(lambda: not ctx.scans.running)
    assert ctx.scans.info["status"] == "done", ctx.scans.info


def test_healthz_and_static(client):
    assert client.get("/healthz").json()["status"] == "ok"
    r = client.get("/")
    assert r.status_code == 200 and "File ExploraViz" in r.text
    assert "Content-Security-Policy" in r.headers


def test_auth_required(client):
    assert client.get("/api/files").status_code == 401
    assert client.get("/api/auth/state").json() == {"auth_disabled": False, "setup_required": False, "user": None}
    r = client.post("/api/auth/login", json={"username": "admin", "password": "faux"})
    assert r.status_code == 401
    login(client)
    cookie = client.cookies.get("fev_session")
    assert cookie
    assert client.get("/api/auth/state").json()["user"] == "admin"
    assert client.get("/api/roots").status_code == 200
    client.post("/api/auth/logout")
    client.cookies.clear()
    assert client.get("/api/roots").status_code == 401


def test_login_cookie_flags(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "motdepasse-test"})
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=strict" in sc


def test_bruteforce_throttled(client):
    for _ in range(3):
        client.post("/api/auth/login", json={"username": "admin", "password": "faux"})
    r = client.post("/api/auth/login", json={"username": "admin", "password": "motdepasse-test"})
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_password_change_invalidates_old_session(client):
    login(client)
    old = client.cookies.get("fev_session")
    r = client.post("/api/auth/password", json={"current": "motdepasse-test", "new": "nouveau-mdp-123"})
    assert r.status_code == 200
    client.cookies.clear()
    client.cookies.set("fev_session", old)
    assert client.get("/api/roots").status_code == 401
    client.cookies.clear()
    login(client, "nouveau-mdp-123")


def test_setup_flow(settings):
    s = dataclasses.replace(settings, admin_password=None)
    with TestClient(create_app(s)) as c:
        assert c.get("/api/auth/state").json()["setup_required"] is True
        assert c.post("/api/auth/setup", json={"username": "moi", "password": "court"}).status_code == 400
        assert c.post("/api/auth/setup", json={"username": "moi", "password": "assez-long-1"}).status_code == 200
        assert c.get("/api/roots").status_code == 200
        assert c.post("/api/auth/setup", json={"username": "x", "password": "assez-long-2"}).status_code == 409


def test_auth_disabled(settings):
    s = dataclasses.replace(settings, auth_disabled=True)
    with TestClient(create_app(s)) as c:
        assert c.get("/api/roots").status_code == 200


def test_scan_then_query(client, settings):
    login(client)
    roots = {r["id"]: r for r in client.get("/api/roots").json()}
    assert set(roots) == {"photos", "docs"}
    scan_and_wait(client)

    cache = settings.cache_dir
    assert (cache / "summary.yaml").exists() and (cache / "index_photos.yaml").exists()
    assert len(list((cache / "history").iterdir())) == 1
    summary = yaml.safe_load((cache / "summary.yaml").read_text(encoding="utf-8"))
    assert summary["totals"]["files"] == 11 and "photos" in summary["trees"]

    r = client.get("/api/files", params={"root": "photos", "sort": "-size", "page_size": 2})
    data = r.json()
    assert data["total"] == 9 and data["pages"] == 5 and data["rows"][0]["name"] == "film.mp4"
    assert data["rows"][0]["category"] == "video"
    r = client.get("/api/files", params={"root": "photos", "path": "2023", "recursive": "false", "ext": "jpg"})
    assert r.json()["total"] == 2
    r = client.get("/api/files", params={"from": "2024-01-01", "category": "pdf,spreadsheet"})
    assert r.json()["total"] == 2
    assert client.get("/api/files", params={"from": "pas-une-date"}).status_code == 400
    assert client.get("/api/files", params={"root": "photos", "path": "../../etc"}).status_code == 400

    st = client.get("/api/stats", params={"root": "photos"}).json()
    assert st["totals"]["files"] == 9 and st["source"] == "index"
    light = client.get("/api/stats", params={"root": "photos", "q": "img", "detail": "false"}).json()
    assert light["totals"]["files"] == 2 and "largest_files" not in light

    tree = client.get("/api/tree", params={"root": "photos"}).json()
    assert [d["name"] for d in tree["dirs"]] == ["2023", "copie", "vide"]
    tm = client.get("/api/treemap", params={"root": "photos", "path": "2023", "depth": 2}).json()
    assert tm["path"] == "2023" and tm["children"]
    assert client.get("/api/treemap", params={"root": "photos", "path": "nope"}).status_code == 404

    ext = client.get("/api/extensions", params={"prefix": "j"}).json()
    assert ext[0] == {"ext": "jpg", "files": 5}

    csv = client.get("/api/files/export.csv", params={"root": "docs"})
    assert csv.status_code == 200
    lines = csv.content.decode("utf-8-sig").strip().splitlines()
    assert lines[0].startswith("racine;chemin") and len(lines) == 3

    hist = client.get("/api/stats/history").json()
    assert len(hist) == 1 and hist[0]["files"] == 11

    status = client.get("/api/scan/status").json()
    assert status["status"] == "done" and status["percent"] == 100.0 and status["next_run"]


def test_scan_single_root_keeps_others(client, settings):
    login(client)
    scan_and_wait(client)
    os.remove(settings.data_dir / "docs" / "rapport.pdf")
    scan_and_wait(client, "photos")  # docs n'est pas rescanné : toujours 2 fichiers
    assert client.get("/api/files", params={"root": "docs"}).json()["total"] == 2
    scan_and_wait(client, "docs")
    assert client.get("/api/files", params={"root": "docs"}).json()["total"] == 1
    assert client.get("/api/files", params={"root": "photos"}).json()["total"] == 9


def test_second_scan_is_rejected_while_running(client):
    login(client)
    ctx = client.app.state.ctx
    assert ctx.scans._lock.acquire()  # simule un scan en cours
    try:
        r = client.post("/api/scan", json={})
        assert r.status_code == 409 and "déjà en cours" in r.json()["detail"]
    finally:
        ctx.scans._lock.release()


def test_config_validation_and_save(client, settings):
    login(client)
    cfg = client.get("/api/config").json()
    assert cfg["scan"]["interval_minutes"] == 15 and "secret" not in cfg["auth"] and "password_hash" not in cfg["auth"]
    r = client.put("/api/config", json={"scan": {**cfg["scan"], "interval_minutes": -5}})
    assert r.status_code == 422
    assert any("scan.interval_minutes" in e for e in r.json()["errors"])
    r = client.put("/api/config", json={"categories": {**cfg["categories"], "code": ["jpg"]}})
    assert r.status_code == 422 and "jpg" in r.json()["errors"][0]
    r = client.put("/api/config", json={"scan": {**cfg["scan"], "interval_minutes": 60}, "ui": {"page_size": 50}})
    assert r.status_code == 200
    on_disk = yaml.safe_load(settings.config_file.read_text(encoding="utf-8"))
    assert on_disk["scan"]["interval_minutes"] == 60 and on_disk["ui"]["page_size"] == 50
    assert on_disk["auth"]["password_hash"].startswith("$2")  # le haché n'est pas perdu


def test_invalid_config_file_fails_clearly(settings):
    settings.config_dir.mkdir(parents=True)
    settings.config_file.write_text("scan:\n  interval_minutes: beaucoup\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        create_app(settings)
    assert "scan.interval_minutes" in str(exc.value)


def test_restart_loads_cache_without_scanning(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        wait_until(app.state.ctx.state.index_ready.is_set)
        login(c)
        scan_and_wait(c)
    app2 = create_app(settings)
    with TestClient(app2) as c:
        wait_until(app2.state.ctx.state.index_ready.is_set)
        login(c)
        assert c.get("/api/files").json()["total"] == 11
        assert app2.state.ctx.scans.info["status"] == "idle"


def test_cache_clear(client, settings):
    login(client)
    scan_and_wait(client)
    r = client.post("/api/cache/clear")
    assert r.status_code == 200 and r.json()["started"]
    ctx = client.app.state.ctx
    wait_until(lambda: not ctx.scans.running)
    assert client.get("/api/files").json()["total"] == 11
