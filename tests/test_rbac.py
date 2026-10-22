"""Comptes multiples et droits par dossier (RBAC).

Arborescence de test (conftest.sample_tree) :
  photos/  a.jpg  b.JPG  notes.txt
           2023/  IMG_001.jpg  IMG_002.jpg  film.mp4   2023/deep/x/y.png
           copie/ a.jpg  vide.dat          vide/
  docs/    rapport.pdf  tableau.xlsx

Le compte « lecteur » n'a accès qu'à photos/2023 : 4 fichiers (IMG_001, IMG_002, film.mp4, y.png).
"""

from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.main import create_app

from .conftest import wait_until

ADMIN_PW = "motdepasse-test"
VIEWER_PW = "lecteur-secret-1"
ALLOWED = {"IMG_001.jpg", "IMG_002.jpg", "film.mp4", "y.png"}
ALLOWED_BYTES = 5000 + 5000 + 100_000 + 300


def login(c: TestClient, user: str, pw: str) -> None:
    c.cookies.clear()
    r = c.post("/api/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text


@pytest.fixture
def app_client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        wait_until(app.state.ctx.state.index_ready.is_set)
        login(c, "admin", ADMIN_PW)
        assert c.post("/api/scan", json={}).status_code == 202
        wait_until(lambda: not app.state.ctx.scans.running)
        r = c.post("/api/users", json={
            "username": "lecteur", "password": VIEWER_PW, "role": "viewer",
            "scopes": [{"root": "photos", "path": "2023"}],
        })
        assert r.status_code == 201, r.text
        yield c


@pytest.fixture
def viewer(app_client):
    login(app_client, "lecteur", VIEWER_PW)
    return app_client


def names(c: TestClient, **params) -> set[str]:
    r = c.get("/api/files", params={"page_size": 1000, **params})
    assert r.status_code == 200, r.text
    return {row["name"] for row in r.json()["rows"]}


# --------------------------------------------------------------------------- confinement des données


def test_viewer_files_are_confined(viewer):
    assert names(viewer) == ALLOWED                                   # sans racine : toutes racines
    assert names(viewer, root="photos") == ALLOWED                    # parent d'un dossier autorisé
    assert names(viewer, root="photos", recursive="false") == set()  # fichiers propres du parent : non
    assert names(viewer, root="photos", path="2023") == ALLOWED
    assert names(viewer, root="photos", path="2023/deep") == {"y.png"}
    assert names(viewer, q="*.jpg") == {"IMG_001.jpg", "IMG_002.jpg"}  # glob : rien hors périmètre
    assert names(viewer, q="a.jpg") == set()
    assert names(viewer, ext="pdf,xlsx") == set()


@pytest.mark.parametrize("params", [
    {"root": "docs"},
    {"root": "photos", "path": "copie"},
    {"root": "photos", "path": "vide"},
    {"root": "photos", "path": "inexistant"},
])
def test_viewer_forbidden_folders_look_nonexistent(viewer, params):
    r = viewer.get("/api/files", params=params).json()
    assert r["total"] == 0 and r["found"] is False  # même réponse qu'un dossier inexistant


def test_viewer_cannot_escape_with_dotdot(viewer):
    assert viewer.get("/api/files", params={"root": "photos", "path": "2023/../copie"}).status_code == 400
    assert viewer.get("/api/tree", params={"root": "photos", "path": "2023/.."}).status_code == 400


def test_viewer_csv_export_is_confined(viewer):
    lines = viewer.get("/api/files/export.csv").content.decode("utf-8-sig").strip().splitlines()
    assert len(lines) == 1 + len(ALLOWED)
    assert all("2023" in line for line in lines[1:])
    lines = viewer.get("/api/files/export.csv", params={"root": "docs"}).content.decode("utf-8-sig").strip().splitlines()
    assert len(lines) == 1


def test_viewer_stats_are_confined(viewer):
    st = viewer.get("/api/stats").json()
    assert st["totals"]["files"] == 4 and st["totals"]["bytes"] == ALLOWED_BYTES
    assert st["totals"]["dirs"] == 3  # 2023, 2023/deep, 2023/deep/x
    assert {f["name"] for f in st["largest_files"]} == ALLOWED
    assert st["duplicates"]["groups"] == 0  # le doublon a.jpg est hors périmètre
    assert st["empty_dirs"]["count"] == 0   # « vide » est hors périmètre
    assert all(d["path"].startswith("2023") for d in st["largest_dirs"])
    assert sum(c["files"] for c in st["categories"]) == 4
    assert viewer.get("/api/stats", params={"root": "docs"}).json()["totals"]["files"] == 0


def test_viewer_extensions_are_confined(viewer):
    exts = {e["ext"]: e["files"] for e in viewer.get("/api/extensions").json()}
    assert exts == {"jpg": 2, "mp4": 1, "png": 1}


def test_viewer_tree_and_roots(viewer):
    roots = viewer.get("/api/roots").json()
    assert [r["id"] for r in roots] == ["photos"]
    assert roots[0]["files"] == 4 and roots[0]["bytes"] == ALLOWED_BYTES
    tree = viewer.get("/api/tree", params={"root": "photos"}).json()
    assert [d["name"] for d in tree["dirs"]] == ["2023"]  # ni « copie » ni « vide »
    assert viewer.get("/api/tree", params={"root": "photos", "path": "copie"}).status_code == 404
    assert viewer.get("/api/tree", params={"root": "docs"}).status_code == 404
    assert [d["name"] for d in viewer.get("/api/tree", params={"root": "photos", "path": "2023"}).json()["dirs"]] == ["deep"]


def test_viewer_treemap_is_confined(viewer):
    tm = viewer.get("/api/treemap", params={"root": "photos"}).json()
    assert tm["size"] == ALLOWED_BYTES
    assert [c["name"] for c in tm["children"]] == ["2023"]
    assert viewer.get("/api/treemap", params={"root": "photos", "path": "copie"}).status_code == 404
    assert viewer.get("/api/treemap", params={"root": "docs"}).status_code == 404
    assert viewer.get("/api/treemap", params={"root": "photos", "path": "2023"}).json()["size"] == ALLOWED_BYTES


def test_viewer_does_not_see_global_history_or_scan_details(viewer):
    assert viewer.get("/api/stats/history").json() == []
    st = viewer.get("/api/scan/status").json()
    for key in ("files", "dirs", "bytes", "current_dir", "current_root", "roots"):
        assert st[key] is None
    assert "roots" not in (st["last_scan"] or {})


def test_nested_scopes_and_scopes_in_two_roots(app_client):
    app_client.put("/api/users/lecteur", json={"scopes": [
        {"root": "photos", "path": "2023"}, {"root": "photos", "path": "2023/deep"}, {"root": "docs", "path": ""},
    ]})
    login(app_client, "lecteur", VIEWER_PW)
    assert names(app_client) == ALLOWED | {"rapport.pdf", "tableau.xlsx"}
    assert app_client.get("/api/stats").json()["totals"]["files"] == 6  # pas de double comptage
    assert {r["id"] for r in app_client.get("/api/roots").json()} == {"photos", "docs"}


# --------------------------------------------------------------------------- routes d'administration


@pytest.mark.parametrize("method,url,body", [
    ("post", "/api/scan", {}),
    ("post", "/api/scan/cancel", None),
    ("get", "/api/config", None),
    ("put", "/api/config", {"ui": {"page_size": 50}}),
    ("post", "/api/cache/clear", None),
    ("get", "/api/users", None),
    ("post", "/api/users", {"username": "x", "password": "12345678"}),
    ("put", "/api/users/lecteur", {"all_folders": True}),
    ("post", "/api/users/lecteur/password", {"password": "nouveau-mdp-9"}),
    ("delete", "/api/users/admin", None),
])
def test_viewer_cannot_use_admin_routes(viewer, method, url, body):
    kwargs = {"json": body} if body is not None else {}
    r = getattr(viewer, method)(url, **kwargs)
    assert r.status_code == 403 and r.json()["code"] == "admin_only"


def test_viewer_can_change_own_password(viewer):
    r = viewer.post("/api/auth/password", json={"current": VIEWER_PW, "new": "autre-secret-22"})
    assert r.status_code == 200
    login(viewer, "lecteur", "autre-secret-22")
    assert viewer.get("/api/auth/state").json()["role"] == "viewer"


def test_all_folders_viewer_sees_everything_but_is_not_admin(app_client):
    app_client.put("/api/users/lecteur", json={"all_folders": True})
    login(app_client, "lecteur", VIEWER_PW)
    assert app_client.get("/api/files").json()["total"] == 11
    assert len(app_client.get("/api/stats/history").json()) == 1
    assert app_client.post("/api/scan", json={}).status_code == 403


# --------------------------------------------------------------------------- gestion des comptes


def test_admin_lists_users_without_secrets(app_client):
    users = {u["username"]: u for u in app_client.get("/api/users").json()}
    assert set(users) == {"admin", "lecteur"}
    assert users["admin"]["is_self"] and users["admin"]["role"] == "admin"
    assert users["lecteur"]["scopes"] == [{"root": "photos", "path": "2023", "found": True}]
    assert all("password_hash" not in u for u in users.values())


@pytest.mark.parametrize("body,status,code", [
    ({"username": "LECTEUR", "password": "12345678"}, 409, "user_exists"),
    ({"username": "pas valide!", "password": "12345678"}, 400, "invalid_username"),
    ({"username": "court", "password": "1234"}, 400, "password_too_short"),
    ({"username": "x1", "password": "12345678", "scopes": [{"root": "inconnue"}]}, 400, "invalid_scope"),
    ({"username": "x2", "password": "12345678", "scopes": [{"root": "photos", "path": "../docs"}]}, 400, "invalid_scope"),
])
def test_create_user_validation(app_client, body, status, code):
    r = app_client.post("/api/users", json=body)
    assert r.status_code == status and r.json()["code"] == code


def test_admin_resets_viewer_password(app_client):
    viewer_client = TestClient(app_client.app)
    login(viewer_client, "lecteur", VIEWER_PW)
    assert viewer_client.get("/api/files").status_code == 200
    r = app_client.post("/api/users/lecteur/password", json={"password": "reinitialise-77"})
    assert r.status_code == 200
    assert viewer_client.get("/api/files").status_code == 401  # ancienne session invalidée
    r = viewer_client.post("/api/auth/login", json={"username": "lecteur", "password": VIEWER_PW})
    assert r.status_code == 401
    login(viewer_client, "lecteur", "reinitialise-77")
    assert viewer_client.get("/api/files").json()["total"] == 4


def test_admin_cannot_reset_another_admin_password(app_client):
    app_client.post("/api/users", json={"username": "admin2", "password": "admin2-secret", "role": "admin"})
    r = app_client.post("/api/users/admin2/password", json={"password": "pirate-123"})
    assert r.status_code == 403 and r.json()["code"] == "reset_admin_forbidden"
    login(app_client, "admin2", "admin2-secret")  # inchangé


def test_scope_change_applies_immediately(app_client):
    viewer_client = TestClient(app_client.app)
    login(viewer_client, "lecteur", VIEWER_PW)
    assert viewer_client.get("/api/files").json()["total"] == 4
    app_client.put("/api/users/lecteur", json={"scopes": [{"root": "docs", "path": ""}]})
    assert names(viewer_client) == {"rapport.pdf", "tableau.xlsx"}
    app_client.put("/api/users/lecteur", json={"scopes": []})
    assert viewer_client.get("/api/files").json()["total"] == 0  # aucun dossier : rien


def test_delete_and_last_admin_rules(app_client):
    assert app_client.delete("/api/users/admin").json()["code"] == "cannot_delete_self"
    assert app_client.put("/api/users/admin", json={"role": "viewer"}).json()["code"] == "last_admin"
    viewer_client = TestClient(app_client.app)
    login(viewer_client, "lecteur", VIEWER_PW)
    assert app_client.delete("/api/users/lecteur").status_code == 200
    assert viewer_client.get("/api/files").status_code == 401  # compte supprimé : session morte
    assert app_client.delete("/api/users/lecteur").status_code == 404
    # Un second administrateur permet de rétrograder le premier.
    app_client.post("/api/users", json={"username": "admin2", "password": "admin2-secret", "role": "admin"})
    assert app_client.put("/api/users/admin2", json={"role": "viewer"}).status_code == 200
    assert app_client.delete("/api/users/admin2").status_code == 200


def test_users_are_persisted(app_client, settings):
    on_disk = yaml.safe_load(settings.config_file.read_text(encoding="utf-8"))
    users = {u["username"]: u for u in on_disk["auth"]["users"]}
    assert users["lecteur"]["role"] == "viewer"
    assert users["lecteur"]["scopes"] == [{"root": "photos", "path": "2023"}]
    assert users["lecteur"]["password_hash"].startswith("$2")


def test_legacy_single_account_is_migrated(settings):
    settings.config_dir.mkdir(parents=True)
    settings.config_file.write_text(yaml.safe_dump({
        "auth": {"username": "ancien", "password_hash": hash_password("ancien-secret"), "session_hours": 12},
    }), encoding="utf-8")
    app = create_app(settings)
    with TestClient(app) as c:
        login(c, "ancien", "ancien-secret")
        assert c.get("/api/auth/state").json()["role"] == "admin"
    on_disk = yaml.safe_load(settings.config_file.read_text(encoding="utf-8"))
    assert "username" not in on_disk["auth"] and on_disk["auth"]["users"][0]["username"] == "ancien"
    assert on_disk["auth"]["session_hours"] == 12


def test_login_is_case_insensitive_on_username(app_client):
    login(app_client, "LECTEUR", VIEWER_PW)
    assert app_client.get("/api/auth/state").json()["user"] == "lecteur"


def test_precomputed_summary_is_not_served_to_restricted_viewers(app_client):
    """Pendant le chargement initial, seuls les comptes sans restriction voient summary.yaml."""
    state = app_client.app.state.ctx.state
    store, state.store = state.store, None  # simule « index en cours de chargement »
    try:
        assert app_client.get("/api/stats").json()["source"] == "summary"  # admin
        assert app_client.get("/api/treemap", params={"root": "photos"}).status_code == 200
        login(app_client, "lecteur", VIEWER_PW)
        assert app_client.get("/api/stats").status_code == 503
        assert app_client.get("/api/treemap", params={"root": "photos"}).status_code == 503
        roots = app_client.get("/api/roots").json()
        assert [r["id"] for r in roots] == ["photos"] and roots[0]["files"] is None
    finally:
        state.store = store


def test_admin_recovery_through_environment(settings):
    """Plus aucun administrateur (entrée retirée de config.yaml) : ADMIN_* en recrée un, sans toucher aux autres."""
    import dataclasses

    settings.config_dir.mkdir(parents=True)
    settings.config_file.write_text(yaml.safe_dump({"auth": {"users": [
        {"username": "lecteur", "password_hash": hash_password(VIEWER_PW), "role": "viewer"},
    ]}}), encoding="utf-8")
    s = dataclasses.replace(settings, admin_user="secours", admin_password="secours-1234")
    app = create_app(s)
    with TestClient(app) as c:
        login(c, "secours", "secours-1234")
        assert c.get("/api/auth/state").json()["role"] == "admin"
        login(c, "lecteur", VIEWER_PW)  # le lecteur est intact
    # ADMIN_USER pointant sur un compte existant non administrateur : rien n'est modifié.
    settings.config_file.write_text(yaml.safe_dump({"auth": {"users": [
        {"username": "lecteur", "password_hash": hash_password(VIEWER_PW), "role": "viewer"},
    ]}}), encoding="utf-8")
    s = dataclasses.replace(settings, admin_user="lecteur", admin_password="secours-1234")
    app = create_app(s)
    users = app.state.ctx.cfg.config.auth.users
    assert [(u.username, u.role) for u in users] == [("lecteur", "viewer")]
