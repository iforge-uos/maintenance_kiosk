"""Exercise Google's real OAuth request construction without external requests."""
from base64 import urlsafe_b64encode
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from kiosk import create_app


CLIENT_FIELDS = {
    "client_id": "test-client.apps.googleusercontent.com",
    "client_secret": "test-private-client-secret",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
}
PENDING_FIELDS = {"google_oauth_state", "google_oauth_redirect", "google_oauth_verifier"}


@pytest.fixture
def oauth(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATA_DIR": tmp_path, "SERVER_NAME": "127.0.0.1:5000"})
    service = app.extensions["kiosk"]
    service.set_secret("google_oauth_client", json.dumps({"web": CLIENT_FIELDS}))
    fixture = SimpleNamespace(
        app=app, service=service, client=app.test_client(), calls=[], token_error=False,
        email=service.get_settings()["google"]["account_email"],
    )

    def send(session, request, **kwargs):
        # Intercept the prepared HTTP request, preserving the real OAuth library
        # code, form encoding and credentials handling before the network boundary.
        fixture.calls.append((request, kwargs))
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = 200
        if request.url == CLIENT_FIELDS["token_uri"]:
            assert request.method == "POST"
            if fixture.token_error:
                response.status_code = 400
                payload = {"error": "invalid_grant", "error_description": "test-private-error"}
            else:
                payload = {"access_token": "test-private-access-token", "refresh_token": "test-private-refresh-token",
                           "token_type": "Bearer", "expires_in": 3600}
        elif request.url == "https://www.googleapis.com/oauth2/v2/userinfo":
            assert request.method == "GET"
            assert request.headers["authorization"] == "Bearer test-private-access-token"
            payload = {"email": fixture.email}
        else:
            pytest.fail(f"Unexpected external request: {urlsplit(request.url).path}")
        response._content = json.dumps(payload).encode()
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    return fixture


def start(oauth):
    response = oauth.client.get("/auth/google/start")
    assert response.status_code == 302
    assert urlsplit(response.location).netloc == "accounts.google.com"
    assert oauth.calls == []
    return parse_qs(urlsplit(response.location).query)


def callback(oauth, authorization, **arguments):
    return oauth.client.get("/auth/google/callback", query_string={
        "state": authorization["state"][0], "code": "test-authorization-code", **arguments,
    })


def assert_flow_consumed(oauth):
    with oauth.client.session_transaction() as session:
        assert PENDING_FIELDS.isdisjoint(session)
        assert session["csrf"]


@pytest.mark.parametrize("client_type", ["web", "installed"])
def test_oauth_token_exchange_reuses_the_original_pkce_verifier(oauth, client_type):
    oauth.service.set_secret("google_oauth_client", json.dumps({client_type: CLIENT_FIELDS}))
    authorization = start(oauth)
    assert authorization["response_type"] == ["code"]
    assert authorization["code_challenge_method"] == ["S256"]
    assert authorization["redirect_uri"] == ["http://127.0.0.1:5000/auth/google/callback"]
    with oauth.client.session_transaction() as session:
        verifier = session["google_oauth_verifier"]
    challenge = urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert authorization["code_challenge"] == [challenge]
    assert verifier not in json.dumps(authorization)

    response = callback(oauth, authorization)

    assert response.location == "/settings?google_connected=1"
    assert len(oauth.calls) == 2
    request, options = oauth.calls[0]
    token_form = parse_qs(request.body)
    assert token_form["code_verifier"] == [verifier]
    assert token_form["redirect_uri"] == authorization["redirect_uri"]
    assert token_form["code"] == ["test-authorization-code"]
    assert options["timeout"] == (5, 20)
    assert oauth.calls[1][1]["timeout"] == 15
    stored = json.loads(oauth.service.get_secret("google_oauth_token"))
    assert stored["refresh_token"] == "test-private-refresh-token"
    assert oauth.service.get_settings()["google"]["auth_mode"] == "oauth"
    assert_flow_consumed(oauth)

    # A completed callback cannot exchange the same authorization code again.
    assert callback(oauth, authorization).status_code == 400
    assert len(oauth.calls) == 2


def test_stale_callback_does_not_consume_a_newer_sign_in(oauth):
    first = start(oauth)
    second = start(oauth)
    assert first["state"] != second["state"]
    assert callback(oauth, first).status_code == 400
    assert oauth.calls == []
    with oauth.client.session_transaction() as session:
        assert session["google_oauth_state"] == second["state"][0]
        assert session["google_oauth_verifier"]
    assert callback(oauth, second).location == "/settings?google_connected=1"
    assert_flow_consumed(oauth)


@pytest.mark.parametrize("arguments", [{"error": "access_denied"}, {"code": ""}])
def test_cancelled_or_missing_code_consumes_flow_without_network_access(oauth, arguments):
    authorization = start(oauth)
    assert callback(oauth, authorization, **arguments).location == "/settings?google_error=cancelled"
    assert oauth.calls == []
    assert oauth.service.get_secret("google_oauth_token") is None
    assert_flow_consumed(oauth)


@pytest.mark.parametrize("missing", ["google_oauth_verifier", "google_oauth_redirect"])
def test_incomplete_pending_flow_requires_a_new_sign_in(oauth, missing):
    authorization = start(oauth)
    with oauth.client.session_transaction() as session:
        session.pop(missing)
    response = callback(oauth, authorization)
    assert response.status_code == 400
    assert "Start again" in response.json["error"]
    assert oauth.calls == []
    assert_flow_consumed(oauth)


@pytest.mark.parametrize("failure", ["token_error", "wrong_account"])
def test_failed_connection_consumes_flow_and_preserves_existing_credential(oauth, failure):
    previous = json.dumps({"client_id": CLIENT_FIELDS["client_id"], "client_secret": CLIENT_FIELDS["client_secret"],
                           "refresh_token": "previous-private-refresh-token"})
    oauth.service.set_secret("google_oauth_token", previous)
    authorization = start(oauth)
    if failure == "token_error":
        oauth.token_error = True
        expected = "connection_failed"
    else:
        oauth.email = "other-account@example.invalid"
        expected = "wrong_account"
    response = callback(oauth, authorization)
    assert response.location == f"/settings?google_error={expected}"
    assert "test-private" not in response.get_data(as_text=True)
    assert oauth.service.get_secret("google_oauth_token") == previous
    assert_flow_consumed(oauth)
