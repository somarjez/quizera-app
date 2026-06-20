import json
import pytest


def test_load_firebase_credential_prefers_env(monkeypatch):
    captured = {}

    fake_cert_payload = {"type": "service_account", "project_id": "demo"}
    monkeypatch.setenv("FIREBASE_CREDENTIALS", json.dumps(fake_cert_payload))

    import importlib
    import config_credentials
    importlib.reload(config_credentials)

    def fake_certificate(arg):
        captured["arg"] = arg
        return "CERT"

    monkeypatch.setattr(config_credentials.credentials, "Certificate",
                        fake_certificate)
    cred = config_credentials.load_firebase_credential()
    assert cred == "CERT"
    assert captured["arg"] == fake_cert_payload


def test_load_firebase_credential_raises_when_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("FIREBASE_CREDENTIALS", raising=False)
    import importlib
    import config_credentials
    importlib.reload(config_credentials)
    monkeypatch.setattr(config_credentials, "LOCAL_KEY_PATH",
                        str(tmp_path / "nope.json"))
    with pytest.raises(RuntimeError):
        config_credentials.load_firebase_credential()
