"""Bravia pairing helpers (mocked HTTP) + skill ownership."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from kageha.devices import bravia as bravia_mod
from kageha.memory.skills import SkillRegistry


class _Resp:
    def __init__(self, status_code: int, payload: dict, cookies: dict | None = None):
        self.status_code = status_code
        self._payload = payload
        self.cookies = httpx.Cookies(cookies or {})
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def test_pair_start_and_finish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.delenv("KAGEHA_BRAVIA_PSK", raising=False)

    def fake_post(url, **kwargs):
        auth = (kwargs.get("headers") or {}).get("Authorization", "")
        if auth:
            assert auth.startswith("Basic ")
            decoded = base64.b64decode(auth.split()[1]).decode()
            assert decoded == ":1234"
            return _Resp(
                200,
                {"result": [], "id": 1},
                cookies={"auth": "cookie-value"},
            )
        if "accessControl" in url:
            return _Resp(401, {"error": [401, "Unauthorized"], "id": 1})
        if "getPowerStatus" in json.dumps(kwargs.get("json") or {}):
            return _Resp(200, {"result": [{"status": "active"}], "id": 1})
        if "getVolumeInformation" in json.dumps(kwargs.get("json") or {}):
            return _Resp(
                200,
                {
                    "result": [
                        [{"target": "speaker", "volume": 10, "mute": False}]
                    ],
                    "id": 1,
                },
            )
        return _Resp(200, {"result": [], "id": 1})

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, **kwargs):
            return fake_post(url, **kwargs)

    monkeypatch.setattr(bravia_mod.httpx, "Client", _Client)

    start = bravia_mod.pair_start("10.0.0.14")
    assert start["awaiting_pin"] is True
    assert start["host"] == "10.0.0.14"

    done = bravia_mod.pair_finish("10.0.0.14", "1234")
    assert done["ok"] is True
    prof = bravia_mod.load_profile("10.0.0.14")
    assert prof is not None
    assert prof.get("paired") is True
    assert prof.get("cookies", {}).get("auth") == "cookie-value"


def test_bravia_is_skill_owned_not_harness_tool():
    skill = SkillRegistry().skills["sony_bravia"]
    assert (skill.path / "scripts" / "key.py").is_file()
    assert (skill.path / "scripts" / "status.py").is_file()
    assert not hasattr(bravia_mod, "register_bravia_tools")
