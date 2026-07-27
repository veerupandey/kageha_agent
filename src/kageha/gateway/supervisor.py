"""Channel gateway — ServiceSupervisor group over existing channel CLIs."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from kageha.config import kageha_home
from kageha.gateway.config import GatewayConfig, load_gateway_config
from kageha.runtime.store import RuntimeStore
from kageha.runtime.supervisor import ServiceDefinition, ServiceSupervisor


class ChannelGateway:
    """Supervise configured channel adapters as a dedicated process group.

    Each channel runs the existing CLI (`python -m kageha.cli telegram`, …)
    under :class:`ServiceSupervisor`. Adapters continue to use the shared
    durable ``RuntimeStore`` / ``DurableChannelQueue`` when they start.
    """

    def __init__(
        self,
        *,
        config: GatewayConfig | None = None,
        store: RuntimeStore | None = None,
        service_root: Path | None = None,
        root: Path | None = None,
    ) -> None:
        self.config = config or load_gateway_config()
        self._owns_store = store is None
        self.store = store or RuntimeStore()
        services = self._build_definitions()
        self.supervisor = ServiceSupervisor(
            store=self.store,
            service_root=service_root,
            root=root or (kageha_home() / "gateway"),
            services=services,
            label_prefix="dev.kageha.gateway",
            unit_prefix="kageha-gateway",
        )

    def close(self) -> None:
        self.supervisor.close()
        if self._owns_store:
            self.store.close()

    def start(self, name: str = "all") -> list[dict[str, Any]]:
        if not self.supervisor.definitions() and name == "all":
            return []
        return self.supervisor.start(name)

    def stop(self, name: str = "all") -> list[dict[str, Any]]:
        return self.supervisor.stop(name)

    def restart(self, name: str = "all") -> list[dict[str, Any]]:
        return self.supervisor.restart(name)

    def status(self) -> dict[str, Any]:
        base = self.supervisor.status()
        enabled = sorted(spec.name for spec in self.config.enabled_channels())
        return {
            **base,
            "gateway": True,
            "config_source": self.config.source,
            "enabled_channels": enabled,
            "known_channels": sorted(self.config.channels.keys()),
        }

    def logs(self, name: str, *, lines: int = 100) -> str:
        return self.supervisor.logs(name, lines=lines)

    def install(self) -> list[Path]:
        return self.supervisor.install()

    def _build_definitions(self) -> list[ServiceDefinition]:
        python = str(Path(sys.executable).resolve())
        out: list[ServiceDefinition] = []
        for spec in self.config.enabled_channels():
            cli = spec.cli_argv()
            command = (
                python,
                "-m",
                "kageha.cli",
                *cli,
            )
            out.append(
                ServiceDefinition(
                    name=spec.name,
                    command=command,
                    enabled=True,
                    restart=True,
                )
            )
        return out
