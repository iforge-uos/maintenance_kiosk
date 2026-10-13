"""Flask application for the iForge maintenance kiosk."""
from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
import secrets

from flask import Flask, jsonify, redirect, render_template, request, session, url_for


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        DATA_DIR=os.environ.get("KIOSK_DATA_DIR", str(Path.cwd() / "instance")),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        JSON_SORT_KEYS=False,
    )
    if config:
        app.config.update(config)
    data_dir = Path(app.config["DATA_DIR"]).resolve()
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    key_path = data_dir / ".session-key"
    try:
        descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "w") as output:
            output.write(secrets.token_hex(32))
            output.flush()
            os.fsync(output.fileno())
    app.secret_key = key_path.read_text()
    from .domain import KioskService
    service = KioskService(data_dir, Path(__file__).resolve().parent.parent / "diagnosis_tree.json")
    app.extensions["kiosk"] = service

    def json_body():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValueError("Send a JSON object.")
        return body

    @app.before_request
    def protect_mutations():
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            token = session.get("csrf", "")
            supplied = request.headers.get("X-CSRF-Token", "")
            if not supplied and not request.is_json:
                supplied = request.form.get("csrf_token", "")
            if not token or not hmac.compare_digest(token, supplied):
                return jsonify(error="This page has expired. Reload it before saving."), 403
        else:
            session.setdefault("csrf", secrets.token_urlsafe(32))

    @app.context_processor
    def template_tokens():
        return {"csrf_token": session.get("csrf", "")}

    @app.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if request.path.startswith("/api/") or request.path.startswith("/auth/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(ValueError)
    def invalid_input(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(413)
    def too_large(error):
        return jsonify(error="The submitted data exceeds the 2 MB limit."), 413

    @app.get("/testing")
    @app.get("/settings")
    def index():
        session.setdefault("csrf", secrets.token_urlsafe(32))
        return render_template("index.html", page=request.path.strip("/") or "dashboard", csrf_token=session["csrf"])

    @app.get("/api/health")
    def health():
        return jsonify(status="ok", storage="local-files", cloud="google-sheets", hardware="not-verified")

    @app.get("/api/state")
    def state():
        return jsonify(
            settings=service.get_settings(), printers=service.printer_summaries(),
            records=service.records(), jobs=service.jobs(), secret_status=service.secret_status(),
            diagnosis=service.get_diagnosis(),
        )

    @app.get("/api/settings")
    def get_settings():
        return jsonify(settings=service.get_settings(), secret_status=service.secret_status())

    @app.put("/api/settings")
    def save_settings():
        return jsonify(settings=service.save_settings(json_body()))

    @app.put("/api/secrets/<path:name>")
    def save_secret(name):
        allowed = {"google_service_account", "google_oauth_client", "google_oauth_token", "smtp_password"}
        allowed.update("discord:" + item["id"] for item in service.get_settings()["discord"]["destinations"])
        if name not in allowed:
            raise ValueError("Unknown credential name. Save its destination first.")
        value = json_body().get("value")
        if not isinstance(value, str):
            raise ValueError("Credential value must be text; use an empty value to remove it.")
        if name.startswith("google_") and value:
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                raise ValueError("Google credentials must be valid JSON.") from None
            if not isinstance(parsed, dict):
                raise ValueError("Google credentials must contain a JSON object.")
            if name == "google_service_account" and parsed.get("type") != "service_account":
                raise ValueError("Select a service account credential file.")
            if name == "google_oauth_client" and not ("web" in parsed or "installed" in parsed):
                raise ValueError("Select an OAuth client JSON file downloaded from Google.")
            if name == "google_oauth_client":
                from urllib.parse import urlsplit
                client = parsed.get("web") or parsed.get("installed")
                if not isinstance(client, dict) or not all(isinstance(client.get(key), str) and client[key] for key in ("client_id", "client_secret", "auth_uri", "token_uri")):
                    raise ValueError("The OAuth client JSON is missing its connection fields.")
                authorization = urlsplit(client["auth_uri"])
                if client["token_uri"] != "https://oauth2.googleapis.com/token" or authorization.scheme != "https" or authorization.netloc != "accounts.google.com":
                    raise ValueError("Use the original OAuth client file with Google's HTTPS endpoints.")
        if name.startswith("discord:") and value:
            from urllib.parse import urlsplit
            url = urlsplit(value)
            if url.scheme != "https" or url.hostname not in {"discord.com", "discordapp.com"} or not url.path.startswith("/api/webhooks/") or url.username or url.password:
                raise ValueError("Use an HTTPS Discord webhook URL.")
        service.set_secret(name, value)
        return jsonify(secret_status=service.secret_status())

    @app.post("/api/weekly")
    def weekly():
        return jsonify(record=service.save_weekly(json_body())), 201

    @app.post("/api/incidents")
    def incidents():
        return jsonify(record=service.save_incident(json_body())), 201

    @app.post("/api/trigger")
    def trigger():
        return jsonify(record=service.trigger(json_body().get("printer_id", ""))), 201

    @app.get("/api/diagnosis")
    def diagnosis():
        return jsonify(diagnosis=service.get_diagnosis())

    @app.put("/api/diagnosis")
    def save_diagnosis():
        return jsonify(diagnosis=service.save_diagnosis(json_body()))

    @app.post("/api/tests/<name>")
    def run_test(name):
        from .diagnostics import run_check
        return jsonify(run_check(name, service, json_body()))

    @app.post("/api/sync")
    def sync():
        from .worker import SyncWorker
        return jsonify(SyncWorker(service, data_dir).run_once(backfill=True, retry_failed=True))

    @app.get("/api/export")
    def export():
        response = jsonify(service.export_data())
        response.headers["Content-Disposition"] = 'attachment; filename="maintenance-records.json"'
        return response

    @app.get("/api/settings/export")
    def export_settings():
        response = jsonify(service.get_settings())
        response.headers["Content-Disposition"] = 'attachment; filename="kiosk-settings.json"'
        return response

    @app.post("/api/settings/import")
    def import_settings():
        return jsonify(settings=service.save_settings(json_body()))

    @app.get("/api/settings/history")
    def settings_history():
        return jsonify(versions=service.settings_history())

    @app.get("/auth/google/start")
    def google_start():
        raw = service.get_secret("google_oauth_client")
        if not raw:
            return redirect("/settings?google_error=client_missing")
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_config(
            json.loads(raw), scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.metadata.readonly", "openid", "https://www.googleapis.com/auth/userinfo.email"],
            redirect_uri=url_for("google_callback", _external=True),
        )
        authorization_url, state = flow.authorization_url(access_type="offline", prompt="consent", login_hint=service.get_settings()["google"]["account_email"])
        session["google_oauth_state"] = state
        session["google_oauth_redirect"] = flow.redirect_uri
        return redirect(authorization_url)

    @app.get("/auth/google/callback")
    def google_callback():
        expected = session.pop("google_oauth_state", "")
        actual = request.args.get("state", "")
        if not expected or not hmac.compare_digest(expected, actual):
            return jsonify(error="Google connection session expired. Start again from Settings."), 400
        if request.args.get("error") or not request.args.get("code"):
            return redirect("/settings?google_error=cancelled")
        from google_auth_oauthlib.flow import Flow
        from google.auth.transport.requests import AuthorizedSession
        try:
            flow = Flow.from_client_config(
                json.loads(service.get_secret("google_oauth_client")),
                scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.metadata.readonly", "openid", "https://www.googleapis.com/auth/userinfo.email"],
                redirect_uri=session.pop("google_oauth_redirect"),
            )
            flow.fetch_token(code=request.args["code"], timeout=(5, 20))
            identity_response = AuthorizedSession(flow.credentials, refresh_timeout=20).get("https://www.googleapis.com/oauth2/v2/userinfo", timeout=15)
            identity_response.raise_for_status()
            identity = identity_response.json()
            settings = service.get_settings()
            if identity.get("email", "").lower() != settings["google"]["account_email"].lower():
                return redirect("/settings?google_error=wrong_account")
            service.set_secret("google_oauth_token", flow.credentials.to_json())
            settings["google"]["auth_mode"] = "oauth"
            service.save_settings(settings)
        except Exception:
            # OAuth errors may contain credentials or authorization codes.
            return redirect("/settings?google_error=connection_failed")
        return redirect("/settings?google_connected=1")

    from .ui import register_ui
    register_ui(app, service)
    return app
