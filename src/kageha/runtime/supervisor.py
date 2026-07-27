"""Cross-platform user-service installation and bounded process supervision."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from kageha.config import kageha_home
from kageha.io import atomic_write_json, atomic_write_text
from kageha.runtime.store import RuntimeStore


@dataclass(frozen=True)
class ServiceDefinition:
    name: str
    command: tuple[str, ...]
    enabled: bool = True
    restart: bool = True


class ServiceSupervisor:
    """Install launchd/systemd definitions and supervise fallback processes."""

    def __init__(
        self,
        *,
        store: RuntimeStore | None = None,
        service_root: Path | None = None,
        root: Path | None = None,
        services: list[ServiceDefinition] | None = None,
        label_prefix: str = "dev.kageha",
        unit_prefix: str = "kageha",
    ) -> None:
        self._owns_store = store is None
        self.store = store or RuntimeStore()
        self.home = kageha_home()
        self.root = root or (self.home / "daemon")
        self.root.mkdir(parents=True, exist_ok=True)
        self.logs_dir = self.root / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self.platform = platform.system()
        self.service_root = service_root or self._default_service_root()
        self._services_override = services
        self.label_prefix = label_prefix
        self.unit_prefix = unit_prefix

    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    def definitions(self) -> list[ServiceDefinition]:
        if self._services_override is not None:
            return [service for service in self._services_override if service.enabled]
        python = str(Path(sys.executable).resolve())
        services = [
            ServiceDefinition(
                "app-server",
                (
                    python,
                    "-m",
                    "kageha.cli",
                    "server",
                    "--listen",
                    "unix://",
                ),
            ),
            ServiceDefinition(
                "memory-worker",
                (
                    python,
                    "-m",
                    "kageha.cli",
                    "memory-worker",
                ),
            ),
        ]
        return [service for service in services if service.enabled]

    def install(self) -> list[Path]:
        self.service_root.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for service in self.definitions():
            if self.platform == "Darwin":
                label = f"{self.label_prefix}.{service.name}"
                path = self.service_root / f"{label}.plist"
                payload = {
                    "Label": label,
                    "ProgramArguments": list(service.command),
                    "RunAtLoad": True,
                    "KeepAlive": {"SuccessfulExit": False},
                    "ThrottleInterval": 5,
                    "StandardOutPath": str(self.logs_dir / f"{service.name}.log"),
                    "StandardErrorPath": str(self.logs_dir / f"{service.name}.err.log"),
                    "EnvironmentVariables": {
                        "KAGEHA_HOME": str(self.home),
                    },
                }
                path.write_bytes(plistlib.dumps(payload, sort_keys=True))
            elif self.platform == "Linux":
                path = self.service_root / f"{self.unit_prefix}-{service.name}.service"
                command = " ".join(_systemd_quote(item) for item in service.command)
                text = "\n".join(
                    [
                        "[Unit]",
                        f"Description=Kageha {service.name}",
                        "After=network-online.target",
                        "",
                        "[Service]",
                        f"ExecStart={command}",
                        f"Environment=KAGEHA_HOME={self.home}",
                        f"WorkingDirectory={Path.cwd()}",
                        "Restart=on-failure",
                        "RestartSec=5",
                        "KillMode=mixed",
                        "",
                        "[Install]",
                        "WantedBy=default.target",
                        "",
                    ]
                )
                atomic_write_text(path, text)
            else:
                raise RuntimeError("daemon services are supported on macOS and Linux")
            paths.append(path)
        atomic_write_json(
            self.root / "installed.json",
            {
                "platform": self.platform,
                                "paths": [str(path) for path in paths],
                "installed_at": time.time(),
            },
        )
        return paths

    def start(self, name: str = "all") -> list[dict[str, Any]]:
        selected = self._select(name)
        return [self._start_direct(service) for service in selected]

    def stop(self, name: str = "all") -> list[dict[str, Any]]:
        state = self._load_state()
        if name == "all":
            # Include historically recorded PIDs so config changes cannot orphan
            # supervised processes.
            selected = set(state.keys()) | {
                service.name for service in self.definitions()
            }
        else:
            selected = {service.name for service in self._select(name)}
        out: list[dict[str, Any]] = []
        for service_name in selected:
            record = dict(state.get(service_name) or {})
            pid = int(record.get("pid") or 0)
            stopped = False
            if pid and _pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGTERM)
                    deadline = time.monotonic() + 5.0
                    while _pid_alive(pid) and time.monotonic() < deadline:
                        time.sleep(0.05)
                    stopped = not _pid_alive(pid)
                except ProcessLookupError:
                    stopped = True
            else:
                stopped = True
            record.update({"state": "stopped", "stopped_at": time.time()})
            state[service_name] = record
            self.store.record_process(
                name=service_name,
                pid=None,
                state="stopped",
                executable=str(record.get("executable") or ""),
                config_hash=str(record.get("config_hash") or ""),
                restart_count=int(record.get("restart_count") or 0),
                detail={"graceful": stopped},
            )
            out.append({"name": service_name, "stopped": stopped, "pid": pid})
        atomic_write_json(self.state_path, state)
        return out

    def restart(self, name: str = "all") -> list[dict[str, Any]]:
        self.stop(name)
        return self.start(name)

    def status(self) -> dict[str, Any]:
        state = self._load_state()
        rows: list[dict[str, Any]] = []
        for service in self.definitions():
            record = dict(state.get(service.name) or {})
            pid = int(record.get("pid") or 0)
            alive = bool(pid and _pid_alive(pid))
            expected_hash = self._config_hash(service)
            rows.append(
                {
                    "name": service.name,
                    "pid": pid or None,
                    "alive": alive,
                    "ready": alive,
                                        "stale_configuration": bool(
                        record and record.get("config_hash") != expected_hash
                    ),
                    "log": str(self.logs_dir / f"{service.name}.log"),
                }
            )
        return {
            "platform": self.platform,
                        "services": rows,
            "duplicate_pids": _duplicates(
                [int(row["pid"]) for row in rows if row.get("pid")]
            ),
        }

    def logs(self, name: str, *, lines: int = 100) -> str:
        paths = [
            self.logs_dir / f"{name}.log",
            self.logs_dir / f"{name}.err.log",
        ]
        chunks: list[str] = []
        for path in paths:
            if not path.is_file():
                continue
            content = path.read_text(errors="replace").splitlines()
            chunks.append(f"== {path} ==\n" + "\n".join(content[-max(1, lines) :]))
        return "\n".join(chunks) or f"No logs for {name}."

    def _start_direct(self, service: ServiceDefinition) -> dict[str, Any]:
        state = self._load_state()
        prior = dict(state.get(service.name) or {})
        prior_pid = int(prior.get("pid") or 0)
        if prior_pid and _pid_alive(prior_pid):
            return {"name": service.name, "pid": prior_pid, "already_running": True}
        log_path = self.logs_dir / f"{service.name}.log"
        err_path = self.logs_dir / f"{service.name}.err.log"
        stdout = log_path.open("ab")
        stderr = err_path.open("ab")
        try:
            process = subprocess.Popen(
                service.command,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
                close_fds=True,
                env={
                    **os.environ,
                    "KAGEHA_HOME": str(self.home),
                },
            )
        finally:
            stdout.close()
            stderr.close()
        config_hash = self._config_hash(service)
        record = {
            "pid": process.pid,
            "state": "running",
                        "executable": service.command[0],
            "command": list(service.command),
            "config_hash": config_hash,
            "restart_count": int(prior.get("restart_count") or 0),
            "started_at": time.time(),
        }
        state[service.name] = record
        atomic_write_json(self.state_path, state)
        self.store.record_process(
            name=service.name,
            pid=process.pid,
            state="running",
            executable=service.command[0],
            config_hash=config_hash,
            restart_count=record["restart_count"],
            detail={"command": list(service.command), "log": str(log_path)},
        )
        return {"name": service.name, "pid": process.pid, "already_running": False}

    def _select(self, name: str) -> list[ServiceDefinition]:
        definitions = self.definitions()
        if name == "all":
            return definitions
        selected = [service for service in definitions if service.name == name]
        if not selected:
            raise KeyError(f"unknown or disabled service: {name}")
        return selected

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {}
        try:
            value = json.loads(self.state_path.read_text())
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _config_hash(self, service: ServiceDefinition) -> str:
        payload = json.dumps(
            {
                **asdict(service),
                                "home": str(self.home),
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def _default_service_root(self) -> Path:
        override = (os.environ.get("KAGEHA_SERVICE_ROOT") or "").strip()
        if override:
            return Path(override).expanduser()
        if self.platform == "Darwin":
            return Path.home() / "Library" / "LaunchAgents"
        if self.platform == "Linux":
            return Path.home() / ".config" / "systemd" / "user"
        return self.root / "services"


def _truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _duplicates(values: list[int]) -> list[int]:
    return sorted({value for value in values if values.count(value) > 1})


def _systemd_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

