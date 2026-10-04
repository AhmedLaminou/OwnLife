from tests.conftest import HEADERS, PASSWORD


def test_status_before_setup(anon):
    r = anon.get("/api/auth/status").json()
    assert r["needs_setup"] is True
    assert r["authenticated"] is False


def test_setup_logs_in_and_seeds_defaults(client):
    status = client.get("/api/auth/status").json()
    assert status["authenticated"] is True
    assert status["needs_setup"] is False
    cats = client.get("/api/categories").json()
    assert any(c["kind"] == "core" for c in cats)
    assert any(c["kind"] == "destructive" and c["is_private"] for c in cats)
    habits = client.get("/api/habits").json()
    assert {h["name"] for h in habits} >= {"Write the journal", "Workout"}


def test_second_account_is_refused_by_default(client):
    r = client.post(
        "/api/auth/setup",
        json={"email": "other@example.test", "display_name": "Other", "password": "another-password-1"},
    )
    assert r.status_code == 403


def test_state_changing_requests_need_the_csrf_header(client):
    r = client.post("/api/auth/logout", headers={"X-OwnLife": ""})
    assert r.status_code == 403
    r = client.post("/api/time/timer/stop", headers={"X-OwnLife": "0"})
    assert r.status_code == 403


def test_logout_then_login(client):
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/profile").status_code == 401
    bad = client.post("/api/auth/login", json={"email": "throwaway@example.test", "password": "wrong-password"})
    assert bad.status_code == 401
    ok = client.post("/api/auth/login", json={"email": "THROWAWAY@example.test", "password": PASSWORD})
    assert ok.status_code == 200
    assert client.get("/api/profile").status_code == 200


def test_login_is_throttled(anon, client):
    client.post("/api/auth/logout")
    codes = [
        anon.post("/api/auth/login", json={"email": "throwaway@example.test", "password": f"nope-{i}"}).status_code
        for i in range(10)
    ]
    assert codes[:8] == [401] * 8
    assert codes[-1] == 429


def test_change_password_logs_out_other_sessions(app, client):
    from fastapi.testclient import TestClient

    with TestClient(app) as other:
        other.headers.update(HEADERS)
        assert other.post("/api/auth/login", json={"email": "throwaway@example.test", "password": PASSWORD}).status_code == 200
        assert other.get("/api/profile").status_code == 200
        r = client.post("/api/auth/change-password",
                        json={"current_password": PASSWORD, "new_password": "a-brand-new-password"})
        assert r.status_code == 200
        assert other.get("/api/profile").status_code == 401  # the other device was logged out
        assert client.get("/api/profile").status_code == 200  # this one stays in


def test_session_cookie_is_http_only(anon):
    r = anon.post(
        "/api/auth/setup",
        json={"email": "cookie@example.test", "display_name": "C", "password": "long-enough-password"},
    )
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_profile_update_validates(client):
    assert client.put("/api/profile", json={"timezone": "Mars/Olympus"}).status_code == 422
    assert client.put("/api/profile", json={"redactions": [{"pattern": "(", "replace": "x"}]}).status_code == 422
    r = client.put("/api/profile", json={"timezone": "Africa/Niamey", "currency": "xof", "wake_target": "5:40"})
    assert r.status_code == 200
    body = r.json()
    assert body["currency"] == "XOF" and body["wake_target"] == "05:40"
