"""File imports use the existing credential API and private credential store."""
import json

import pytest

from kiosk import create_app


CLIENT_FIELDS = {
    "client_id": "test-client-id",
    "client_secret": "private-client-secret",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
}
SERVICE_ACCOUNT = {
    "type": "service_account",
    "client_email": "kiosk@example.iam.gserviceaccount.com",
    "private_key": "private-key-placeholder",
    "token_uri": "https://oauth2.googleapis.com/token",
}
AUTHORIZED_USER = {
    "type": "authorized_user",
    "client_id": "test-client-id",
    "client_secret": "private-client-secret",
    "refresh_token": "private-refresh-token",
}
WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/private-webhook-token"
HEADERS = {"X-CSRF-Token": "import-test-csrf"}


@pytest.fixture
def app(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail("Saving a credential must not call an external service.")

    monkeypatch.setattr("requests.sessions.Session.request", no_network)
    return create_app({"TESTING": True, "DATA_DIR": tmp_path})


@pytest.fixture
def client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf"] = "import-test-csrf"
    return client


def save(client, name, value):
    if isinstance(value, dict):
        value = json.dumps(value)
    return client.put(f"/api/secrets/{name}", json={"value": value}, headers=HEADERS)


@pytest.mark.parametrize(("name", "credential", "private_text"), [
    ("google_oauth_client", {"web": CLIENT_FIELDS}, "private-client-secret"),
    ("google_oauth_client", {"installed": CLIENT_FIELDS}, "private-client-secret"),
    ("google_service_account", SERVICE_ACCOUNT, "private-key-placeholder"),
    ("google_oauth_token", AUTHORIZED_USER, "private-refresh-token"),
    ("google_oauth_token", {key: value for key, value in AUTHORIZED_USER.items() if key != "type"}, "private-refresh-token"),
    ("discord:maintenance", WEBHOOK, "private-webhook-token"),
])
def test_credentials_persist_privately_without_changing_settings(client, app, name, credential, private_text):
    original = client.get("/api/settings").json["settings"]
    response = save(client, name, credential)
    assert response.status_code == 200, response.json
    assert response.json["secret_status"][name] is True
    assert private_text not in response.get_data(as_text=True)
    assert client.get("/api/settings").json["settings"] == original

    restarted = create_app({"TESTING": True, "DATA_DIR": app.config["DATA_DIR"]})
    expected = json.dumps(credential) if isinstance(credential, dict) else credential
    assert restarted.extensions["kiosk"].get_secret(name) == expected
    assert (app.config["DATA_DIR"] / "secrets.json").stat().st_mode & 0o777 == 0o600
    for endpoint in ("/api/state", "/api/settings", "/api/export", "/api/settings/export", "/api/settings/history"):
        response = restarted.test_client().get(endpoint)
        assert response.status_code == 200
        assert private_text not in response.get_data(as_text=True)


@pytest.mark.parametrize(("name", "valid", "invalid"), [
    ("google_oauth_client", {"web": CLIENT_FIELDS}, SERVICE_ACCOUNT),
    ("google_oauth_client", {"web": CLIENT_FIELDS}, {"web": CLIENT_FIELDS, "installed": CLIENT_FIELDS}),
    ("google_oauth_client", {"web": CLIENT_FIELDS}, {"web": None}),
    ("google_oauth_client", {"web": CLIENT_FIELDS}, {"web": {**CLIENT_FIELDS, "client_secret": " "}}),
    ("google_oauth_client", {"web": CLIENT_FIELDS}, {"web": {**CLIENT_FIELDS, "client_id": 123}}),
    ("google_oauth_client", {"web": CLIENT_FIELDS}, {"web": {**CLIENT_FIELDS, "auth_uri": "https://[invalid/private-secret"}}),
    ("google_oauth_client", {"web": CLIENT_FIELDS}, {"web": {**CLIENT_FIELDS, "auth_uri": "https://accounts.google.com.evil.invalid/private-secret"}}),
    ("google_oauth_client", {"web": CLIENT_FIELDS}, {"web": {**CLIENT_FIELDS, "token_uri": "https://evil.invalid/private-secret"}}),
    ("google_service_account", SERVICE_ACCOUNT, {"web": CLIENT_FIELDS}),
    ("google_service_account", SERVICE_ACCOUNT, {"type": "service_account"}),
    ("google_service_account", SERVICE_ACCOUNT, {**SERVICE_ACCOUNT, "private_key": False}),
    ("google_service_account", SERVICE_ACCOUNT, {**SERVICE_ACCOUNT, "token_uri": "https://evil.invalid/private-secret"}),
    ("google_oauth_token", AUTHORIZED_USER, SERVICE_ACCOUNT),
    ("google_oauth_token", AUTHORIZED_USER, {"type": "authorized_user"}),
    ("google_oauth_token", AUTHORIZED_USER, {**AUTHORIZED_USER, "refresh_token": " "}),
    ("google_oauth_token", AUTHORIZED_USER, {**AUTHORIZED_USER, "token_uri": "https://evil.invalid/private-secret"}),
    ("google_oauth_token", AUTHORIZED_USER, "[]"),
    ("google_oauth_token", AUTHORIZED_USER, '{"refresh_token": "private-secret"'),
    ("discord:maintenance", WEBHOOK, "https://discord.com/api/webhooks/123456789"),
    ("discord:maintenance", WEBHOOK, "https://discord.com/api/webhooks/123456789/private-secret/messages/1"),
    ("discord:maintenance", WEBHOOK, "https://discord.com.evil.invalid/api/webhooks/123456789/private-secret"),
    ("discord:maintenance", WEBHOOK, "https://user:private-secret@discord.com/api/webhooks/123456789/private-secret"),
    ("discord:maintenance", WEBHOOK, "https://discord.com:invalid/api/webhooks/123456789/private-secret"),
    ("discord:maintenance", WEBHOOK, "https://[invalid/private-secret"),
    ("discord:maintenance", WEBHOOK, WEBHOOK + "?redirect=private-secret"),
    ("discord:maintenance", WEBHOOK, WEBHOOK + "?thread_id=private-secret"),
])
def test_rejected_credentials_do_not_replace_existing_value_or_expose_contents(client, app, name, valid, invalid):
    assert save(client, name, valid).status_code == 200
    expected = app.extensions["kiosk"].get_secret(name)
    response = save(client, name, invalid)
    assert response.status_code == 400
    assert "private-" not in response.get_data(as_text=True)
    assert app.extensions["kiosk"].get_secret(name) == expected


@pytest.mark.parametrize("url", [
    WEBHOOK + "?wait=false&thread_id=123456789012345678",
    WEBHOOK.replace("discord.com", "discordapp.com"),
    WEBHOOK.replace("discord.com", "canary.discord.com"),
    WEBHOOK.replace("discord.com", "ptb.discord.com"),
    WEBHOOK.replace("discord.com", "discord.com:443"),
    WEBHOOK.replace("/api/", "/api/v10/") + "/",
])
def test_valid_discord_variants_keep_the_imported_url(client, app, url):
    assert save(client, "discord:maintenance", url).status_code == 200
    assert app.extensions["kiosk"].get_secret("discord:maintenance") == url


@pytest.mark.parametrize(("name", "credential"), [
    ("google_oauth_client", {"web": CLIENT_FIELDS}),
    ("google_service_account", SERVICE_ACCOUNT),
    ("google_oauth_token", AUTHORIZED_USER),
    ("discord:maintenance", WEBHOOK),
])
def test_empty_credential_still_removes_saved_value(client, app, name, credential):
    assert save(client, name, credential).status_code == 200
    response = save(client, name, "")
    assert response.status_code == 200
    assert response.json["secret_status"][name] is False
    assert not app.extensions["kiosk"].get_secret(name)


def test_credential_save_still_requires_csrf_and_saved_destination(client, app):
    response = client.put("/api/secrets/discord:maintenance", json={"value": WEBHOOK})
    assert response.status_code == 403
    assert save(client, "discord:unknown", WEBHOOK).status_code == 400
    assert not app.extensions["kiosk"].get_secret("discord:maintenance")


@pytest.mark.parametrize("initial_thread", ["", "?thread_id=123456789012345678"])
def test_pending_discord_jobs_keep_the_same_thread_when_credentials_rotate(client, app, initial_thread):
    assert save(client, "discord:maintenance", WEBHOOK + initial_thread).status_code == 200
    service = app.extensions["kiosk"]
    settings = service.get_settings()
    settings["discord"]["enabled"] = True
    service.save_settings(settings)
    service.trigger("core-one-01")
    original_jobs = service.jobs()
    assert any(job["kind"] == "discord" for job in original_jobs)

    replacement = WEBHOOK.replace("private-webhook-token", "replacement-private-token") + "/"
    replacement += (initial_thread + "&" if initial_thread else "?") + "wait=false"
    assert save(client, "discord:maintenance", replacement).status_code == 200
    assert service.jobs() == original_jobs

    for different_thread in ("?thread_id=987654321098765432", "" if initial_thread else "?thread_id=123456789012345678"):
        response = save(client, "discord:maintenance", WEBHOOK + different_thread)
        assert response.status_code == 400
        assert "outstanding jobs" in response.json["error"]
        assert service.get_secret("discord:maintenance") == replacement
        assert service.jobs() == original_jobs


@pytest.mark.parametrize("url", [
    WEBHOOK.replace("discord.com", "discord.com:invalid"),
    WEBHOOK.replace("discord.com", "discord.com:80"),
    "https://[invalid/private-secret",
    WEBHOOK + "?thread_id=123&thread_id=456",
    WEBHOOK + "?thread_id=",
    WEBHOOK + "?redirect=private-secret",
])
def test_domain_discord_validation_rejects_bad_ports_and_queries_without_secret_errors(app, url):
    with pytest.raises(ValueError) as caught:
        app.extensions["kiosk"].set_secret("discord:maintenance", url)
    assert "private-" not in str(caught.value)
