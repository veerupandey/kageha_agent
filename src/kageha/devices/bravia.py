"""Sony Bravia IP control library (skill-owned; not a harness tool pack).

Pairing (Normal auth on TV):
  1. skill_run sony_bravia scripts/pair_start.py
  2. skill_run sony_bravia scripts/pair_finish.py --pin XXXX
  3. skill_run sony_bravia scripts/key.py / launch.py / status.py

Optional: KAGEHA_BRAVIA_PSK / KAGEHA_BRAVIA_HOST skip pairing.
"""

from __future__ import annotations

import base64
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

import httpx

from kageha.config import kageha_home
from kageha.devices.android_tv import discover_tv_candidates

_CLIENT_NICKNAME = "Kageha"


def _auth_dir() -> Path:
    d = kageha_home() / "bravia"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _host() -> str:
    return (
        os.environ.get("KAGEHA_BRAVIA_HOST")
        or os.environ.get("KAGEHA_ANDROID_TV_HOST")
        or ""
    ).strip()


def _psk() -> str:
    return (os.environ.get("KAGEHA_BRAVIA_PSK") or "").strip()


def _profile_path(host: str) -> Path:
    safe = re.sub(r"[^\w.\-]+", "_", host.strip())
    return _auth_dir() / f"{safe}.json"


def load_profile(host: str | None = None) -> dict[str, Any] | None:
    h = (host or _host()).strip()
    if not h:
        return None
    path = _profile_path(h)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def save_profile(host: str, data: dict[str, Any]) -> Path:
    path = _profile_path(host)
    path.write_text(json.dumps(data, indent=2) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def _register_params(client_id: str, nickname: str) -> list[Any]:
    return [
        {
            "clientid": client_id,
            "nickname": nickname,
            "level": "private",
        },
        [
            {"value": "false", "function": "WOL"},
            {"value": "true", "function": "pinRegistration"},
        ],
    ]


class BraviaClient:
    def __init__(self, host: str, *, psk: str = "", cookies: dict[str, str] | None = None):
        self.host = host.strip()
        self.psk = psk.strip()
        self.cookies = dict(cookies or {})
        self.base = f"http://{self.host}/sony"
        self._ircc_cache: dict[str, str] | None = None

    def _headers(self, *, soap: bool = False) -> dict[str, str]:
        if soap:
            h = {
                "Content-Type": "text/xml; charset=UTF-8",
                "SOAPACTION": '"urn:schemas-sony-com:service:IRCC:1#X_SendIRCC"',
            }
        else:
            h = {"Content-Type": "application/json; charset=UTF-8"}
        if self.psk:
            h["X-Auth-PSK"] = self.psk
        return h

    def rpc(
        self,
        service: str,
        method: str,
        params: list[Any] | None = None,
        *,
        version: str = "1.0",
        auth_basic: str | None = None,
        timeout: float = 8.0,
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        headers = self._headers()
        if auth_basic is not None:
            headers["Authorization"] = auth_basic
        with httpx.Client(timeout=timeout, cookies=self.cookies) as client:
            r = client.post(
                f"{self.base}/{service}",
                json={
                    "method": method,
                    "id": 1,
                    "params": params if params is not None else [],
                    "version": version,
                },
                headers=headers,
            )
            # Merge any new cookies
            for k, v in r.cookies.items():
                self.cookies[k] = v
            try:
                data = r.json()
            except Exception:  # noqa: BLE001
                data = {"error": [r.status_code, r.text[:300]]}
            if not isinstance(data, dict):
                data = {"error": [r.status_code, "non-json"]}
            return r.status_code, data, dict(r.cookies)

    def ircc(self, code: str, timeout: float = 8.0) -> tuple[int, str]:
        soap = (
            '<?xml version="1.0"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            "<s:Body>"
            '<u:X_SendIRCC xmlns:u="urn:schemas-sony-com:service:IRCC:1">'
            f"<IRCCCode>{code}</IRCCCode>"
            "</u:X_SendIRCC>"
            "</s:Body></s:Envelope>"
        )
        with httpx.Client(timeout=timeout, cookies=self.cookies) as client:
            r = client.post(
                f"{self.base}/IRCC",
                content=soap,
                headers=self._headers(soap=True),
            )
            for k, v in r.cookies.items():
                self.cookies[k] = v
            return r.status_code, r.text[:400]

    def remote_codes(self) -> dict[str, str]:
        if self._ircc_cache is not None:
            return self._ircc_cache
        code, data, _ = self.rpc("system", "getRemoteControllerInfo")
        out: dict[str, str] = {}
        if code < 400 and isinstance(data.get("result"), list) and len(data["result"]) >= 2:
            for item in data["result"][1]:
                if isinstance(item, dict) and item.get("name") and item.get("value"):
                    out[str(item["name"])] = str(item["value"])
        self._ircc_cache = out
        return out

    def power_status(self) -> str:
        _, data, _ = self.rpc("system", "getPowerStatus")
        result = data.get("result")
        if isinstance(result, list) and result and isinstance(result[0], dict):
            return str(result[0].get("status") or "")
        return ""

    def volume_info(self) -> dict[str, Any]:
        _, data, _ = self.rpc("audio", "getVolumeInformation")
        result = data.get("result")
        if isinstance(result, list) and result and isinstance(result[0], list) and result[0]:
            first = result[0][0]
            if isinstance(first, dict):
                return first
        return {}


def client_from_env(host: str | None = None) -> BraviaClient | None:
    h = (host or _host()).strip()
    if not h:
        return None
    psk = _psk()
    cookies: dict[str, str] = {}
    client_id = ""
    nickname = _CLIENT_NICKNAME
    prof = load_profile(h)
    if prof:
        cookies = {str(k): str(v) for k, v in (prof.get("cookies") or {}).items()}
        client_id = str(prof.get("client_id") or "")
        nickname = str(prof.get("nickname") or nickname)
        if not psk:
            psk = str(prof.get("psk") or "")
    c = BraviaClient(h, psk=psk, cookies=cookies)
    c._client_id = client_id  # type: ignore[attr-defined]
    c._nickname = nickname  # type: ignore[attr-defined]
    return c


def pair_start(host: str, *, force_new: bool = True) -> dict[str, Any]:
    """Trigger PIN on TV. Returns pending profile info.

    Always uses a fresh client id by default — Bravia often won't re-show a PIN
    for the same client after a dismissed/expired challenge.
    """
    h = host.strip()
    existing = load_profile(h) or {}
    if force_new or not existing.get("client_id") or existing.get("paired"):
        client_id = f"{_CLIENT_NICKNAME}:{uuid.uuid4()}"
        nickname = _CLIENT_NICKNAME
    else:
        client_id = str(existing.get("client_id"))
        nickname = str(existing.get("nickname") or _CLIENT_NICKNAME)
    # Prefer the simple payload that reliably returns HTTP 401 (PIN challenge)
    # on consumer Bravia; fall back to pinRegistration variant.
    payloads = [
        [
            {"clientid": client_id, "nickname": nickname},
            [{"value": "yes", "function": "WOL"}],
        ],
        _register_params(client_id, nickname),
    ]
    client = BraviaClient(h, psk=_psk(), cookies={})
    status, data = 0, {}
    for params in payloads:
        status, data, _ = client.rpc("accessControl", "actRegister", params)
        err = data.get("error")
        if status == 401 or (isinstance(err, list) and err and err[0] == 401):
            break
        if status < 400 and "error" not in data:
            break
    err = data.get("error")
    pending = {
        "host": h,
        "client_id": client_id,
        "nickname": nickname,
        "awaiting_pin": True,
        "http_status": status,
        "tv_error": err,
    }
    save_profile(
        h,
        {
            "host": h,
            "client_id": client_id,
            "nickname": nickname,
            "cookies": {},
            "paired": False,
        },
    )
    if status == 401 or (isinstance(err, list) and err and err[0] == 401):
        pending["message"] = (
            f"PIN should be on the TV (device: {nickname}). "
            f"Enter it with: kageha bravia pair --host {h} --pin XXXX\n"
            "If no PIN: dismiss any old pairing dialog, or remove 'Kageha' under "
            "Settings → Network → Remote device settings → Registered remote devices, "
            "then retry. XR models can use Pre-Shared Key instead."
        )
        return pending
    if status < 400 and "error" not in data:
        pending["awaiting_pin"] = False
        pending["paired"] = True
        pending["message"] = "Already authorized (no PIN needed)."
        return pending
    pending["awaiting_pin"] = False
    pending["message"] = (
        f"TV rejected pairing ({status}, {err}). "
        "Often means a prior PIN dialog is stuck or rate-limited: "
        "power-cycle the TV, remove registered 'Kageha' devices, "
        "or set IP control → Pre-Shared Key and use KAGEHA_BRAVIA_PSK."
    )
    return pending


def pair_finish(host: str, pin: str) -> dict[str, Any]:
    h = host.strip()
    pin = (pin or "").strip()
    if not pin:
        return {"ok": False, "error": "empty pin"}
    prof = load_profile(h) or {}
    nickname = str(prof.get("nickname") or _CLIENT_NICKNAME)
    basic = "Basic " + base64.b64encode(f":{pin}".encode()).decode("ascii")
    # Try current profile client first, then alternate payloads. Bravia binds the
    # PIN to the clientid from the challenge that showed it — a later force_new
    # start can leave a stale profile that must not be the only attempt.
    client_ids = []
    if prof.get("client_id"):
        client_ids.append(str(prof["client_id"]))
    # Keep last successful / prior ids from disk sidecar if any
    hist = prof.get("prior_client_ids") or []
    if isinstance(hist, list):
        client_ids.extend(str(x) for x in hist if x)
    # de-dupe
    seen: set[str] = set()
    ordered: list[str] = []
    for cid in client_ids:
        if cid not in seen:
            seen.add(cid)
            ordered.append(cid)
    if not ordered:
        ordered = [f"{_CLIENT_NICKNAME}:{uuid.uuid4()}"]

    last_err: Any = None
    last_status = 0
    for client_id in ordered:
        payloads = [
            [
                {"clientid": client_id, "nickname": nickname},
                [{"value": "yes", "function": "WOL"}],
            ],
            _register_params(client_id, nickname),
            [
                {"clientid": client_id, "nickname": nickname},
                [{"function": "WOL", "value": "no"}],
            ],
        ]
        for params in payloads:
            client = BraviaClient(h, psk=_psk(), cookies={})
            status, data, new_cookies = client.rpc(
                "accessControl",
                "actRegister",
                params,
                auth_basic=basic,
            )
            cookies = dict(client.cookies)
            cookies.update(new_cookies)
            last_status, last_err = status, data.get("error") or data
            ok = status < 400 and "error" not in data
            if not ok and not cookies:
                continue
            path = save_profile(
                h,
                {
                    "host": h,
                    "client_id": client_id,
                    "nickname": nickname,
                    "cookies": cookies,
                    "paired": True,
                    "prior_client_ids": ordered,
                },
            )
            client.cookies = cookies
            return {
                "ok": True,
                "host": h,
                "profile": str(path),
                "cookies": list(cookies.keys()),
                "power": client.power_status(),
                "volume": client.volume_info(),
                "message": f"Paired. Set KAGEHA_BRAVIA_HOST={h} if not already.",
            }
    return {
        "ok": False,
        "http_status": last_status,
        "error": last_err,
        "hint": (
            "Wrong PIN, expired challenge, or mismatched client id. "
            "Run skill_run sony_bravia scripts/pair_start.py once, enter the NEW pin "
            "immediately (do not start pairing again before finishing)."
        ),
    }


def resolve_host(explicit: str = "") -> str:
    h = (explicit or _host()).strip()
    if h:
        return h
    report = discover_tv_candidates()
    bravia = report.get("bravia_hosts") or []
    if len(bravia) == 1:
        return str(bravia[0]["host"])
    if bravia:
        return str(bravia[0]["host"])
    return ""


def status_report(host: str = "") -> dict[str, Any]:
    h = resolve_host(host)
    out: dict[str, Any] = {
        "host": h,
        "psk_env": bool(_psk()),
        "paired": False,
    }
    if not h:
        report = discover_tv_candidates()
        out["discover"] = report
        out["error"] = "No host configured"
        return out
    prof = load_profile(h)
    out["paired"] = bool(prof and prof.get("paired") and prof.get("cookies"))
    out["profile"] = str(_profile_path(h)) if prof else ""
    client = client_from_env(h)
    assert client is not None
    try:
        out["power"] = client.power_status()
        out["volume"] = client.volume_info()
        code, data, _ = client.rpc("appControl", "getApplicationList")
        out["apps_auth_ok"] = code < 400 and "error" not in data
        if not out["apps_auth_ok"]:
            out["apps_error"] = data.get("error")
            out["hint"] = (
                "Not authorized. skill_run sony_bravia scripts/pair_start.py "
                "then pair_finish.py --pin …, or set KAGEHA_BRAVIA_PSK."
            )
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out


def send_key(key: str, host: str = "") -> dict[str, Any]:
    h = resolve_host(host)
    if not h:
        return {"ok": False, "error": "no Bravia host"}
    name = (key or "").strip()
    aliases = {
        "ok": "Confirm",
        "center": "Confirm",
        "enter": "Confirm",
        "select": "Confirm",
        "vol_up": "VolumeUp",
        "volume_up": "VolumeUp",
        "vol_down": "VolumeDown",
        "volume_down": "VolumeDown",
    }
    lookup = aliases.get(name.lower().replace(" ", "_"), name)
    client = client_from_env(h)
    assert client is not None
    codes = client.remote_codes()
    code = codes.get(lookup)
    if not code:
        for k, v in codes.items():
            if k.lower() == lookup.lower():
                code = v
                lookup = k
                break
    if not code:
        sample = ", ".join(sorted(codes)[:30])
        return {"ok": False, "error": f"unknown key {key!r}. Examples: {sample}"}
    status, text = client.ircc(code)
    if status >= 400:
        return {
            "ok": False,
            "error": f"IRCC {status}: {text}. Re-pair or set PSK.",
        }
    prof = load_profile(h) or {
        "host": h,
        "client_id": "",
        "nickname": _CLIENT_NICKNAME,
    }
    prof["cookies"] = client.cookies
    prof["paired"] = True
    save_profile(h, prof)
    return {"ok": True, "key": lookup, "host": h}


def list_apps(query: str = "", host: str = "") -> dict[str, Any]:
    h = resolve_host(host)
    if not h:
        return {"ok": False, "error": "no Bravia host"}
    client = client_from_env(h)
    assert client is not None
    code, data, _ = client.rpc("appControl", "getApplicationList")
    if code >= 400 or "error" in data:
        return {
            "ok": False,
            "error": data.get("error"),
            "hint": "Pair first via sony_bravia skill scripts",
        }
    apps = data.get("result") or []
    if isinstance(apps, list) and apps and isinstance(apps[0], list):
        apps = apps[0]
    rows = []
    q = query.strip().lower()
    for a in apps:
        if not isinstance(a, dict):
            continue
        title = str(a.get("title") or a.get("name") or "")
        uri = str(a.get("uri") or "")
        if q and q not in title.lower() and q not in uri.lower():
            continue
        rows.append({"title": title, "uri": uri})
    return {"ok": True, "count": len(rows), "apps": rows[:80]}


def launch_app(name: str = "", uri: str = "", host: str = "") -> dict[str, Any]:
    h = resolve_host(host)
    if not h:
        return {"ok": False, "error": "no Bravia host"}
    target = (uri or "").strip()
    client = client_from_env(h)
    assert client is not None
    if not target and name.strip():
        listed = list_apps(query=name, host=h)
        apps = listed.get("apps") or []
        if not apps:
            return {
                "ok": False,
                "error": f"no app matched {name!r}",
            }
        target = str(apps[0].get("uri") or "")
    if not target:
        return {"ok": False, "error": "provide name= or uri="}
    code, data, _ = client.rpc("appControl", "setActiveApp", [{"uri": target}])
    if code >= 400 or "error" in data:
        return {"ok": False, "error": data.get("error") or data}
    return {"ok": True, "uri": target, "host": h}
