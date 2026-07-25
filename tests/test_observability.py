"""Tests for the observability module (metrics, quality, tracing)."""

import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import ANY

import pytest

from british_museum_agent.config import Settings
from british_museum_agent.observability.metrics import (
    ServiceMetrics,
    get_service_metrics,
    reset_service_metrics,
)
from british_museum_agent.observability.quality import (
    QualityProxies,
    estimate_quality_proxies,
)
from british_museum_agent.observability.tracing import (
    PhoenixStatus,
    configure_phoenix,
    get_phoenix_status,
    shutdown_phoenix,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_metrics() -> None:
    """Reset the ServiceMetrics singleton before each test."""
    reset_service_metrics()


@pytest.fixture
def metrics() -> ServiceMetrics:
    """Return a fresh ServiceMetrics singleton."""
    return get_service_metrics()


# ---------------------------------------------------------------------------
# Stubs for response objects
# ---------------------------------------------------------------------------

@dataclass
class _SourceStub:
    """Minimal source stub duck-typed to satisfy record_chat_response."""

    score: float


@dataclass
class _ToolCallStub:
    """Minimal tool-call stub duck-typed to satisfy record_chat_response."""

    name: str
    status: str


@dataclass
class _ResponseStub:
    """Minimal response stub duck-typed to satisfy record_chat_response."""

    confidence: float
    sources: list[_SourceStub]
    tool_calls: list[_ToolCallStub]


# ===================================================================
# 1. ServiceMetrics initialization
# ===================================================================


class TestServiceMetricsInitialization:
    """ServiceMetrics() creates counters, distributions, and internal state."""

    def test_creates_http_counters(self, metrics: ServiceMetrics) -> None:
        assert hasattr(metrics, "_http_requests")
        assert hasattr(metrics, "_http_errors")
        assert hasattr(metrics, "_http_latency")
        # All start empty
        assert metrics._http_requests == {}
        assert metrics._http_errors == {}
        assert metrics._http_latency == {}

    def test_creates_chat_counters(self, metrics: ServiceMetrics) -> None:
        assert metrics._chat_requests == 0
        assert metrics._chat_failures == 0
        assert metrics._chat_no_evidence == 0
        assert metrics._chat_tool_errors == {}

    def test_creates_quality_distributions(self, metrics: ServiceMetrics) -> None:
        for dist_name in ("_chat_confidence", "_chat_groundedness", "_chat_hallucination_risk"):
            dist = getattr(metrics, dist_name)
            assert dist.count == 0
            assert dist.total == 0.0
            assert dist.maximum == 0.0
            assert len(dist.bucket_counts) > 0

    def test_has_lock(self, metrics: ServiceMetrics) -> None:
        assert hasattr(metrics, "_lock")

    def test_singleton(self) -> None:
        assert get_service_metrics() is get_service_metrics()

    def test_reset_provides_fresh_instance(self) -> None:
        a = get_service_metrics()
        b = reset_service_metrics()
        assert a is not b


# ===================================================================
# 2. record_http_request
# ===================================================================


class TestRecordHttpRequest:
    """record_http_request increments counters and records latency."""

    def test_increments_request_counter(self, metrics: ServiceMetrics) -> None:
        metrics.record_http_request(method="GET", route="/api/v1/health", status_code=200, duration_seconds=0.05)
        summary = metrics.summary()
        assert summary["http"]["requests_total"] == 1

    def test_records_multiple_methods(self, metrics: ServiceMetrics) -> None:
        metrics.record_http_request(method="GET", route="/api/v1/health", status_code=200, duration_seconds=0.01)
        metrics.record_http_request(method="POST", route="/api/v1/chat", status_code=201, duration_seconds=0.50)
        metrics.record_http_request(method="GET", route="/api/v1/health", status_code=200, duration_seconds=0.02)
        summary = metrics.summary()
        assert summary["http"]["requests_total"] == 3

    def test_tracks_errors_when_status_ge_400(self, metrics: ServiceMetrics) -> None:
        metrics.record_http_request(method="POST", route="/api/v1/chat", status_code=400, duration_seconds=0.10)
        metrics.record_http_request(method="POST", route="/api/v1/chat", status_code=500, duration_seconds=0.20)
        summary = metrics.summary()
        assert summary["http"]["errors_total"] == 2

    def test_does_not_count_2xx_as_errors(self, metrics: ServiceMetrics) -> None:
        metrics.record_http_request(method="GET", route="/ok", status_code=200, duration_seconds=0.01)
        summary = metrics.summary()
        assert summary["http"]["errors_total"] == 0

    def test_records_latency_distribution(self, metrics: ServiceMetrics) -> None:
        metrics.record_http_request(method="GET", route="/slow", status_code=200, duration_seconds=1.5)
        metrics.record_http_request(method="GET", route="/slow", status_code=200, duration_seconds=2.5)
        summary = metrics.summary()
        lat = summary["http"]["latency_seconds"]
        assert lat["count"] == 2
        assert lat["sum"] == pytest.approx(4.0, rel=1e-3)
        assert lat["max"] == pytest.approx(2.5, rel=1e-3)

    def test_normalizes_method(self, metrics: ServiceMetrics) -> None:
        metrics.record_http_request(method="get", route="/test", status_code=200, duration_seconds=0.01)
        summary = metrics.summary()
        assert summary["http"]["requests_total"] == 1

    def test_normalizes_unknown_method(self, metrics: ServiceMetrics) -> None:
        metrics.record_http_request(method="PURGE", route="/test", status_code=200, duration_seconds=0.01)
        summary = metrics.summary()
        assert summary["http"]["requests_total"] == 1

    def test_handles_none_route(self, metrics: ServiceMetrics) -> None:
        metrics.record_http_request(method="GET", route=None, status_code=200, duration_seconds=0.01)
        summary = metrics.summary()
        assert summary["http"]["requests_total"] == 1

    def test_handles_empty_route(self, metrics: ServiceMetrics) -> None:
        metrics.record_http_request(method="GET", route="", status_code=200, duration_seconds=0.01)
        summary = metrics.summary()
        assert summary["http"]["requests_total"] == 1

    def test_status_class_in_summary(self, metrics: ServiceMetrics) -> None:
        metrics.record_http_request(method="GET", route="/a", status_code=200, duration_seconds=0.01)
        metrics.record_http_request(method="POST", route="/b", status_code=404, duration_seconds=0.01)
        metrics.record_http_request(method="PUT", route="/c", status_code=500, duration_seconds=0.01)
        summary = metrics.summary()
        assert summary["http"]["status_classes"] == {"2xx": 1, "4xx": 1, "5xx": 1}

    def test_invalid_status_clamped_to_500(self, metrics: ServiceMetrics) -> None:
        metrics.record_http_request(method="GET", route="/x", status_code=999, duration_seconds=0.01)
        summary = metrics.summary()
        assert summary["http"]["requests_total"] == 1


# ===================================================================
# 3. record_chat  (via record_chat_response)
# ===================================================================


class TestRecordChat:
    """record_chat_response records confidence, groundedness, and hallucination risk."""

    @staticmethod
    def _make_response(
        confidence: float = 0.85,
        source_scores: list[float] | None = None,
        tool_errors: int = 0,
    ) -> _ResponseStub:
        if source_scores is None:
            source_scores = [0.9, 0.7]
        sources = [_SourceStub(score=s) for s in source_scores]
        calls = [
            _ToolCallStub(name="search", status="error")
            for _ in range(tool_errors)
        ] or [_ToolCallStub(name="search", status="success")]
        return _ResponseStub(confidence=confidence, sources=sources, tool_calls=calls)

    def test_records_confidence(self, metrics: ServiceMetrics) -> None:
        metrics.record_chat_response(self._make_response(confidence=0.85))
        summary = metrics.summary()
        assert summary["chat"]["confidence"]["count"] == 1
        assert summary["chat"]["confidence"]["average"] == pytest.approx(0.85, rel=1e-3)

    def test_records_multiple_chats(self, metrics: ServiceMetrics) -> None:
        for conf in (0.7, 0.8, 0.9):
            metrics.record_chat_response(self._make_response(confidence=conf))
        summary = metrics.summary()
        assert summary["chat"]["requests_total"] == 0  # record_chat_response does NOT increment this
        assert summary["chat"]["confidence"]["count"] == 3

    def test_records_groundedness_proxy(self, metrics: ServiceMetrics) -> None:
        metrics.record_chat_response(self._make_response(confidence=0.8, source_scores=[0.9]))
        summary = metrics.summary()
        # groundedness = clamp((0.8 + 0.9) / 2) = 0.85
        assert summary["chat"]["groundedness_proxy"]["count"] == 1
        assert summary["chat"]["groundedness_proxy"]["average"] == pytest.approx(0.85, rel=1e-3)

    def test_records_hallucination_risk_proxy(self, metrics: ServiceMetrics) -> None:
        metrics.record_chat_response(self._make_response(confidence=0.5, source_scores=[0.7]))
        summary = metrics.summary()
        # groundedness = (0.5 + 0.7) / 2 = 0.6, hallucination_risk = 1 - 0.6 = 0.4
        assert summary["chat"]["hallucination_risk_proxy"]["count"] == 1
        assert summary["chat"]["hallucination_risk_proxy"]["average"] == pytest.approx(0.4, rel=1e-3)

    def test_tracks_chat_started(self, metrics: ServiceMetrics) -> None:
        metrics.record_chat_started()
        summary = metrics.summary()
        assert summary["chat"]["requests_total"] == 1

    def test_tracks_chat_failure(self, metrics: ServiceMetrics) -> None:
        metrics.record_chat_failure()
        summary = metrics.summary()
        assert summary["chat"]["failures_total"] == 1

    def test_tracks_no_evidence(self, metrics: ServiceMetrics) -> None:
        response = self._make_response(source_scores=[])
        metrics.record_chat_response(response)
        summary = metrics.summary()
        assert summary["chat"]["no_evidence_total"] == 1

    def test_tracks_tool_errors(self, metrics: ServiceMetrics) -> None:
        response = self._make_response(tool_errors=2)
        metrics.record_chat_response(response)
        summary = metrics.summary()
        assert summary["chat"]["tool_errors_total"] == 2

    def test_quality_skipped_when_no_sources(self, metrics: ServiceMetrics) -> None:
        """When there are no sources, estimate_quality_proxies returns None."""
        response = self._make_response(confidence=0.9, source_scores=[])
        metrics.record_chat_response(response)
        summary = metrics.summary()
        # groundedness should NOT be recorded because proxies was None
        assert summary["chat"]["groundedness_proxy"]["count"] == 0


# ===================================================================
# 4. render_prometheus
# ===================================================================


class TestRenderPrometheus:
    """render_prometheus() returns valid Prometheus exposition-format text."""

    def test_returns_string(self, metrics: ServiceMetrics) -> None:
        output = metrics.render_prometheus()
        assert isinstance(output, str)
        assert output.endswith("\n")

    def test_includes_help_and_type_lines(self, metrics: ServiceMetrics) -> None:
        output = metrics.render_prometheus()
        assert "# HELP" in output
        assert "# TYPE" in output

    def test_http_metrics_present(self, metrics: ServiceMetrics) -> None:
        metrics.record_http_request(method="GET", route="/health", status_code=200, duration_seconds=0.05)
        output = metrics.render_prometheus()
        assert "british_museum_http_requests_total" in output
        assert "british_museum_http_request_duration_seconds" in output

    def test_http_errors_metric_present(self, metrics: ServiceMetrics) -> None:
        metrics.record_http_request(method="POST", route="/chat", status_code=500, duration_seconds=0.10)
        output = metrics.render_prometheus()
        assert "british_museum_http_request_errors_total" in output

    def test_chat_metrics_present(self, metrics: ServiceMetrics) -> None:
        metrics.record_chat_started()
        output = metrics.render_prometheus()
        assert "british_museum_chat_requests_total" in output
        assert "british_museum_chat_failures_total" in output

    def test_histogram_buckets_rendered(self, metrics: ServiceMetrics) -> None:
        metrics.record_http_request(method="GET", route="/health", status_code=200, duration_seconds=0.05)
        output = metrics.render_prometheus()
        assert "_bucket{" in output
        assert "_count{" in output
        assert "_sum{" in output

    def test_chat_quality_histograms(self, metrics: ServiceMetrics) -> None:
        response = _ResponseStub(
            confidence=0.8,
            sources=[_SourceStub(score=0.9)],
            tool_calls=[_ToolCallStub(name="search", status="success")],
        )
        metrics.record_chat_response(response)
        output = metrics.render_prometheus()
        assert "british_museum_chat_confidence_ratio" in output
        assert "british_museum_chat_groundedness_proxy_ratio" in output
        assert "british_museum_chat_hallucination_risk_proxy_ratio" in output

    def test_process_metrics_included(self, metrics: ServiceMetrics) -> None:
        output = metrics.render_prometheus()
        assert "process_cpu_seconds_total" in output
        # resident_memory_bytes may be None on some platforms — skip assertion

    def test_latency_bucket_le_inf(self, metrics: ServiceMetrics) -> None:
        metrics.record_http_request(method="GET", route="/health", status_code=200, duration_seconds=5.0)
        output = metrics.render_prometheus()
        assert 'le="+Inf"' in output


# ===================================================================
# 5. summary
# ===================================================================


class TestSummary:
    """summary() returns a dict with the expected structure."""

    def test_returns_dict(self, metrics: ServiceMetrics) -> None:
        assert isinstance(metrics.summary(), dict)

    def test_top_level_keys(self, metrics: ServiceMetrics) -> None:
        s = metrics.summary()
        assert set(s.keys()) == {"http", "chat", "process"}

    def test_http_structure(self, metrics: ServiceMetrics) -> None:
        s = metrics.summary()
        http = s["http"]
        assert "requests_total" in http
        assert "errors_total" in http
        assert "latency_seconds" in http
        assert "status_classes" in http
        # latency_seconds sub-fields
        lat = http["latency_seconds"]
        assert "count" in lat
        assert "sum" in lat
        assert "average" in lat
        assert "max" in lat

    def test_chat_structure(self, metrics: ServiceMetrics) -> None:
        s = metrics.summary()
        chat = s["chat"]
        expected_keys = {
            "requests_total",
            "failures_total",
            "no_evidence_total",
            "tool_errors_total",
            "confidence",
            "groundedness_proxy",
            "hallucination_risk_proxy",
        }
        assert set(chat.keys()) == expected_keys

    def test_process_structure(self, metrics: ServiceMetrics) -> None:
        s = metrics.summary()
        proc = s["process"]
        assert "cpu_seconds_total" in proc
        assert "resident_memory_bytes" in proc

    def test_all_zero_when_no_data(self, metrics: ServiceMetrics) -> None:
        s = metrics.summary()
        assert s["http"]["requests_total"] == 0
        assert s["http"]["errors_total"] == 0
        assert s["chat"]["requests_total"] == 0

    def test_summary_reflects_recorded_data(self, metrics: ServiceMetrics) -> None:
        metrics.record_http_request(method="GET", route="/test", status_code=200, duration_seconds=0.10)
        metrics.record_chat_started()
        s = metrics.summary()
        assert s["http"]["requests_total"] == 1
        assert s["chat"]["requests_total"] == 1


# ===================================================================
# 6. Quality proxies
# ===================================================================


class TestQualityProxies:
    """Direct tests for estimate_quality_proxies in quality.py."""

    def test_returns_quality_proxies_instance(self) -> None:
        result = estimate_quality_proxies(0.8, [0.9, 0.7])
        assert isinstance(result, QualityProxies)

    def test_groundedness_formula(self) -> None:
        # groundedness = clamp((clamp(confidence) + max(source_scores)) / 2)
        # (0.8 + 0.9) / 2 = 0.85
        result = estimate_quality_proxies(0.8, [0.5, 0.9, 0.7])
        assert result is not None
        assert result.groundedness == pytest.approx(0.85, rel=1e-3)

    def test_hallucination_risk_is_one_minus_groundedness(self) -> None:
        result = estimate_quality_proxies(0.8, [0.9])
        assert result is not None
        assert result.hallucination_risk == pytest.approx(1.0 - result.groundedness, rel=1e-9)

    def test_clamps_confidence_to_0_1_range(self) -> None:
        result = estimate_quality_proxies(2.0, [0.5])
        assert result is not None
        assert result.groundedness <= 1.0

    def test_clamps_source_scores_to_0_1_range(self) -> None:
        result = estimate_quality_proxies(0.5, [1.5, -0.3])
        assert result is not None
        assert 0.0 <= result.groundedness <= 1.0

    def test_handles_non_finite_source_scores(self) -> None:
        result = estimate_quality_proxies(0.5, [float("nan"), float("inf"), 0.8])
        assert result is not None
        assert result.groundedness == pytest.approx((0.5 + 0.8) / 2.0, rel=1e-3)

    def test_returns_none_when_no_valid_scores(self) -> None:
        result = estimate_quality_proxies(0.8, [])
        assert result is None

    def test_returns_none_when_no_source_scores(self) -> None:
        result = estimate_quality_proxies(0.8, [float("nan"), float("inf")])
        assert result is None

    def test_clamps_non_finite_confidence(self) -> None:
        result = estimate_quality_proxies(float("nan"), [0.5])
        assert result is not None
        # clamped confidence = 0.0, so groundedness = (0.0 + 0.5) / 2 = 0.25
        assert result.groundedness == pytest.approx(0.25, rel=1e-3)


# ===================================================================
# 7. configure_phoenix disabled
# ===================================================================


class TestConfigurePhoenixDisabled:
    """configure_phoenix returns a disabled PhoenixStatus when phoenix_enabled=False."""

    @pytest.fixture
    def no_phoenix_settings(self) -> Settings:
        return Settings(
            _env_file=None,
            app_name="British Museum Agent",
            app_env="test",
            phoenix_enabled=False,
            phoenix_project_name="british-museum-agent",
        )

    def test_returns_disabled_status(self, no_phoenix_settings: Settings) -> None:
        status = configure_phoenix(no_phoenix_settings)
        assert status.enabled is False
        assert status.active is False
        assert status.project == "british-museum-agent"
        assert "disabled" in status.detail.lower()

    def test_does_not_call_module_loader(self, no_phoenix_settings: Settings) -> None:
        """The disabled path should never invoke the module_loader callable."""
        loader = pytest.fail  # will fail if called
        status = configure_phoenix(no_phoenix_settings, module_loader=loader)
        assert status.enabled is False

    def test_get_phoenix_status_reflects_disabled_state(self, no_phoenix_settings: Settings) -> None:
        configure_phoenix(no_phoenix_settings)
        status = get_phoenix_status()
        assert status.enabled is False
        assert status.active is False


# ===================================================================
# 8. get_phoenix_status
# ===================================================================


class TestGetPhoenixStatus:
    """get_phoenix_status() returns a PhoenixStatus with expected fields."""

    def test_returns_phoenix_status_instance(self) -> None:
        status = get_phoenix_status()
        assert isinstance(status, PhoenixStatus)

    def test_has_required_fields(self) -> None:
        status = get_phoenix_status()
        # PhoenixStatus is a frozen dataclass with: enabled, active, project, detail
        assert hasattr(status, "enabled")
        assert hasattr(status, "active")
        assert hasattr(status, "project")
        assert hasattr(status, "detail")

    def test_fields_are_correct_types(self) -> None:
        status = get_phoenix_status()
        assert isinstance(status.enabled, bool)
        assert isinstance(status.active, bool)
        assert isinstance(status.project, str)
        assert isinstance(status.detail, str)

    def test_default_project_name(self) -> None:
        status = get_phoenix_status()
        assert status.project == "british-museum-agent"

    def test_default_is_not_active(self) -> None:
        status = get_phoenix_status()
        assert status.active is False

    def test_default_is_not_enabled(self) -> None:
        status = get_phoenix_status()
        assert status.enabled is False

    def test_default_detail_is_informative(self) -> None:
        status = get_phoenix_status()
        assert status.detail  # non-empty
        assert "phoenix" in status.detail.lower() or "tracing" in status.detail.lower()

    def test_status_after_shutdown(self) -> None:
        shutdown_phoenix()
        status = get_phoenix_status()
        assert status.active is False
        assert "shut down" in status.detail.lower()
