"""Local-first metrics/traces with optional OpenTelemetry export."""

from __future__ import annotations

import contextlib
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterator

from kageha.config import otlp_endpoint
from kageha.runtime.store import RuntimeStore


@dataclass
class Span:
    telemetry: "Telemetry"
    id: str
    started: float
    session_id: str
    turn_id: str
    status: str = "ok"

    def fail(self) -> None:
        self.status = "error"


class Telemetry:
    def __init__(self, store: RuntimeStore) -> None:
        self.store = store
        self.endpoint = otlp_endpoint()
        self._otel_tracer: Any = None
        if self.endpoint:
            self._configure_otlp()

    def metric(
        self,
        name: str,
        value: float,
        *,
        unit: str = "1",
        session_id: str = "",
        turn_id: str = "",
        labels: dict[str, Any] | None = None,
    ) -> str:
        return self.store.record_metric(
            name,
            value,
            unit=unit,
            session_id=session_id,
            turn_id=turn_id,
            labels=labels,
        )

    @contextlib.contextmanager
    def span(
        self,
        name: str,
        *,
        session_id: str = "",
        turn_id: str = "",
        attributes: dict[str, Any] | None = None,
    ) -> Iterator[Span]:
        trace_id = turn_id or session_id or uuid.uuid4().hex
        span_id = self.store.start_span(
            name,
            trace_id=trace_id,
            session_id=session_id,
            turn_id=turn_id,
            attributes=attributes,
        )
        span = Span(self, span_id, time.perf_counter(), session_id, turn_id)
        otel_context = (
            self._otel_tracer.start_as_current_span(name, attributes=attributes)
            if self._otel_tracer is not None
            else contextlib.nullcontext()
        )
        try:
            with otel_context:
                yield span
        except Exception:
            span.fail()
            raise
        finally:
            elapsed_ms = (time.perf_counter() - span.started) * 1000.0
            self.store.finish_span(
                span.id,
                status=span.status,
                attributes={"duration_ms": elapsed_ms},
            )
            self.metric(
                f"{name}.duration",
                elapsed_ms,
                unit="ms",
                session_id=session_id,
                turn_id=turn_id,
                labels={"status": span.status},
            )

    def _configure_otlp(self) -> None:
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider(
                resource=Resource.create({"service.name": "kageha"})
            )
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=self.endpoint))
            )
            trace.set_tracer_provider(provider)
            self._otel_tracer = trace.get_tracer("kageha.runtime")
        except Exception:  # noqa: BLE001
            # Local telemetry remains authoritative when exporter setup is absent.
            self._otel_tracer = None

