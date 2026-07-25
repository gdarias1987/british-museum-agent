from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from importlib import import_module
from threading import Lock
from typing import Any, Callable

from british_museum_agent.observability.quality import estimate_quality_proxies


@dataclass(frozen=True)
class PhoenixStatus:
    enabled: bool
    active: bool
    project: str
    detail: str


@dataclass
class _PhoenixRuntime:
    status: PhoenixStatus
    tracer: Any = None
    tracer_provider: Any = None
    instrumentor: Any = None


class _ChatSpan(AbstractContextManager["_ChatSpan"]):
    def __init__(self, tracer: Any, trace_id: str):
        self._tracer = tracer
        self._trace_id = trace_id
        self._context: Any = None
        self._span: Any = None

    def __enter__(self) -> "_ChatSpan":
        if self._tracer is not None:
            self._context = self._tracer.start_as_current_span("chat.answer")
            self._span = self._context.__enter__()
            self._span.set_attribute("chat.trace_id", self._trace_id)
        return self

    def record_response(self, response: Any) -> None:
        if self._span is None:
            return
        sources = list(response.sources)
        tool_error_count = sum(
            1
            for call in response.tool_calls
            if str(getattr(call, "status", "")).casefold() != "success"
        )
        self._span.set_attribute("chat.confidence", float(response.confidence))
        self._span.set_attribute("chat.sources.count", len(sources))
        self._span.set_attribute("chat.no_evidence", not sources)
        self._span.set_attribute("chat.tool_errors.count", tool_error_count)
        proxies = estimate_quality_proxies(
            float(response.confidence),
            (float(source.score) for source in sources),
        )
        if proxies is not None:
            self._span.set_attribute(
                "chat.groundedness_proxy",
                proxies.groundedness,
            )
            self._span.set_attribute(
                "chat.hallucination_risk_proxy",
                proxies.hallucination_risk,
            )

    def record_error(self, error: BaseException) -> None:
        if self._span is None:
            return
        self._span.set_attribute("error.type", type(error).__name__)
        self._span.set_attribute("error.message.recorded", False)

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        if self._context is not None:
            return bool(self._context.__exit__(exc_type, exc_value, traceback))
        return False


_runtime_lock = Lock()
_runtime = _PhoenixRuntime(
    PhoenixStatus(
        enabled=False,
        active=False,
        project="british-museum-agent",
        detail="Phoenix tracing has not been configured.",
    )
)


def configure_phoenix(
    settings: Any,
    *,
    module_loader: Callable[[str], Any] = import_module,
) -> PhoenixStatus:
    """Configure an OTLP exporter and OpenInference LangChain instrumentation."""
    global _runtime
    project = str(settings.phoenix_project_name)
    if not settings.phoenix_enabled:
        status = PhoenixStatus(
            enabled=False,
            active=False,
            project=project,
            detail="Phoenix tracing is disabled.",
        )
        with _runtime_lock:
            _shutdown_runtime(_runtime)
            _runtime = _PhoenixRuntime(status)
        return status

    try:
        resources = module_loader("opentelemetry.sdk.resources")
        sdk_trace = module_loader("opentelemetry.sdk.trace")
        sdk_export = module_loader("opentelemetry.sdk.trace.export")
        otlp_export = module_loader(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter"
        )
        openinference = module_loader("openinference.instrumentation")
        langchain_instrumentation = module_loader(
            "openinference.instrumentation.langchain"
        )

        resource = resources.Resource.create(
            {
                "service.name": str(settings.app_name),
                "deployment.environment.name": str(settings.app_env),
                "openinference.project.name": project,
            }
        )
        tracer_provider = sdk_trace.TracerProvider(resource=resource)
        api_key = settings.phoenix_api_key_value
        headers = {"api_key": api_key} if api_key else None
        exporter = otlp_export.OTLPSpanExporter(
            endpoint=settings.resolved_phoenix_collector_endpoint,
            headers=headers,
            timeout=3,
        )
        tracer_provider.add_span_processor(
            sdk_export.BatchSpanProcessor(exporter)
        )
        trace_config = openinference.TraceConfig(
            hide_inputs=True,
            hide_outputs=True,
        )
        instrumentor = langchain_instrumentation.LangChainInstrumentor()
        instrumentor.instrument(
            tracer_provider=tracer_provider,
            config=trace_config,
        )
        tracer = tracer_provider.get_tracer("british_museum_agent.observability")
    except Exception as exc:
        status = PhoenixStatus(
            enabled=True,
            active=False,
            project=project,
            detail=f"Phoenix tracing is unavailable ({type(exc).__name__}).",
        )
        with _runtime_lock:
            _shutdown_runtime(_runtime)
            _runtime = _PhoenixRuntime(status)
        return status

    status = PhoenixStatus(
        enabled=True,
        active=True,
        project=project,
        detail="Phoenix OTLP/OpenInference tracing is active with content capture disabled.",
    )
    with _runtime_lock:
        _shutdown_runtime(_runtime)
        _runtime = _PhoenixRuntime(
            status=status,
            tracer=tracer,
            tracer_provider=tracer_provider,
            instrumentor=instrumentor,
        )
    return status


def get_phoenix_status() -> PhoenixStatus:
    with _runtime_lock:
        return _runtime.status


def trace_chat(trace_id: str) -> _ChatSpan:
    with _runtime_lock:
        tracer = _runtime.tracer if _runtime.status.active else None
    return _ChatSpan(tracer, trace_id)


def shutdown_phoenix() -> None:
    global _runtime
    with _runtime_lock:
        status = _runtime.status
        _shutdown_runtime(_runtime)
        _runtime = _PhoenixRuntime(
            PhoenixStatus(
                enabled=status.enabled,
                active=False,
                project=status.project,
                detail="Phoenix tracing is shut down.",
            )
        )


def _shutdown_runtime(runtime: _PhoenixRuntime) -> None:
    if runtime.instrumentor is not None:
        try:
            runtime.instrumentor.uninstrument()
        except Exception:
            pass
    if runtime.tracer_provider is not None:
        try:
            runtime.tracer_provider.shutdown()
        except Exception:
            pass
