from __future__ import annotations

import os
import re
import sys
from bisect import bisect_left
from dataclasses import dataclass, field
from functools import lru_cache
from threading import Lock
from time import process_time
from typing import Any

from british_museum_agent.observability.quality import estimate_quality_proxies

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_RATIO_BUCKETS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
_SAFE_TOOL_NAME = re.compile(r"[^a-zA-Z0-9_.:-]+")


@dataclass
class _Distribution:
    buckets: tuple[float, ...]
    count: int = 0
    total: float = 0.0
    maximum: float = 0.0
    bucket_counts: list[int] = field(init=False)

    def __post_init__(self) -> None:
        self.bucket_counts = [0] * len(self.buckets)

    def observe(self, value: float) -> None:
        value = max(0.0, float(value))
        self.count += 1
        self.total += value
        self.maximum = max(self.maximum, value)
        index = bisect_left(self.buckets, value)
        if index < len(self.bucket_counts):
            self.bucket_counts[index] += 1

    def as_summary(self) -> dict[str, float | int]:
        return {
            "count": self.count,
            "sum": self.total,
            "average": self.total / self.count if self.count else 0.0,
            "max": self.maximum,
        }


class ServiceMetrics:
    """Small in-process Prometheus registry with bounded, privacy-safe labels."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._http_requests: dict[tuple[str, str, str], int] = {}
        self._http_errors: dict[tuple[str, str, str], int] = {}
        self._http_latency: dict[tuple[str, str], _Distribution] = {}
        self._chat_requests = 0
        self._chat_failures = 0
        self._chat_no_evidence = 0
        self._chat_tool_errors: dict[str, int] = {}
        self._chat_confidence = _Distribution(_RATIO_BUCKETS)
        self._chat_groundedness = _Distribution(_RATIO_BUCKETS)
        self._chat_hallucination_risk = _Distribution(_RATIO_BUCKETS)

    def record_http_request(
        self,
        *,
        method: str,
        route: str | None,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        safe_method = _normalize_method(method)
        safe_route = _normalize_route(route)
        safe_status = str(status_code if 100 <= status_code <= 599 else 500)
        request_key = (safe_method, safe_route, safe_status)
        latency_key = (safe_method, safe_route)
        with self._lock:
            self._http_requests[request_key] = self._http_requests.get(request_key, 0) + 1
            if int(safe_status) >= 400:
                self._http_errors[request_key] = self._http_errors.get(request_key, 0) + 1
            distribution = self._http_latency.setdefault(
                latency_key,
                _Distribution(_LATENCY_BUCKETS),
            )
            distribution.observe(duration_seconds)

    def record_chat_started(self) -> None:
        with self._lock:
            self._chat_requests += 1

    def record_chat_failure(self) -> None:
        with self._lock:
            self._chat_failures += 1

    def record_chat_response(self, response: Any) -> None:
        confidence = float(response.confidence)
        sources = list(response.sources)
        tool_errors = [
            call
            for call in response.tool_calls
            if str(getattr(call, "status", "")).casefold() != "success"
        ]
        proxies = estimate_quality_proxies(
            confidence,
            (float(source.score) for source in sources),
        )

        with self._lock:
            self._chat_confidence.observe(confidence)
            if not sources:
                self._chat_no_evidence += 1
            for call in tool_errors:
                tool_name = _normalize_tool_name(getattr(call, "name", "unknown"))
                self._chat_tool_errors[tool_name] = (
                    self._chat_tool_errors.get(tool_name, 0) + 1
                )
            if proxies is not None:
                self._chat_groundedness.observe(proxies.groundedness)
                self._chat_hallucination_risk.observe(proxies.hallucination_risk)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            requests_total = sum(self._http_requests.values())
            errors_total = sum(self._http_errors.values())
            latency = _merge_distributions(self._http_latency.values(), _LATENCY_BUCKETS)
            status_classes: dict[str, int] = {}
            for (_, _, status_code), count in self._http_requests.items():
                status_class = f"{status_code[0]}xx"
                status_classes[status_class] = status_classes.get(status_class, 0) + count
            tool_errors_total = sum(self._chat_tool_errors.values())
            payload = {
                "http": {
                    "requests_total": requests_total,
                    "errors_total": errors_total,
                    "latency_seconds": latency.as_summary(),
                    "status_classes": dict(sorted(status_classes.items())),
                },
                "chat": {
                    "requests_total": self._chat_requests,
                    "failures_total": self._chat_failures,
                    "no_evidence_total": self._chat_no_evidence,
                    "tool_errors_total": tool_errors_total,
                    "confidence": self._chat_confidence.as_summary(),
                    "groundedness_proxy": self._chat_groundedness.as_summary(),
                    "hallucination_risk_proxy": self._chat_hallucination_risk.as_summary(),
                },
            }
        payload["process"] = _process_metrics()
        return payload

    def render_prometheus(self) -> str:
        with self._lock:
            http_requests = dict(self._http_requests)
            http_errors = dict(self._http_errors)
            http_latency = {
                key: _copy_distribution(value) for key, value in self._http_latency.items()
            }
            chat_requests = self._chat_requests
            chat_failures = self._chat_failures
            chat_no_evidence = self._chat_no_evidence
            chat_tool_errors = dict(self._chat_tool_errors)
            chat_confidence = _copy_distribution(self._chat_confidence)
            chat_groundedness = _copy_distribution(self._chat_groundedness)
            chat_hallucination_risk = _copy_distribution(self._chat_hallucination_risk)

        lines = [
            "# HELP british_museum_http_requests_total HTTP requests completed.",
            "# TYPE british_museum_http_requests_total counter",
        ]
        for (method, route, status_code), value in sorted(http_requests.items()):
            labels = _labels(method=method, route=route, status_code=status_code)
            lines.append(f"british_museum_http_requests_total{labels} {value}")

        lines.extend(
            [
                "# HELP british_museum_http_request_errors_total HTTP responses with status >= 400.",
                "# TYPE british_museum_http_request_errors_total counter",
            ]
        )
        for (method, route, status_code), value in sorted(http_errors.items()):
            labels = _labels(method=method, route=route, status_code=status_code)
            lines.append(f"british_museum_http_request_errors_total{labels} {value}")

        lines.extend(
            [
                "# HELP british_museum_http_request_duration_seconds HTTP request latency.",
                "# TYPE british_museum_http_request_duration_seconds histogram",
            ]
        )
        for (method, route), distribution in sorted(http_latency.items()):
            lines.extend(
                _render_histogram(
                    "british_museum_http_request_duration_seconds",
                    distribution,
                    {"method": method, "route": route},
                )
            )

        lines.extend(
            [
                "# HELP british_museum_chat_requests_total Chat requests handled by ChatService.",
                "# TYPE british_museum_chat_requests_total counter",
                f"british_museum_chat_requests_total {chat_requests}",
                "# HELP british_museum_chat_failures_total Chat requests that raised before a response.",
                "# TYPE british_museum_chat_failures_total counter",
                f"british_museum_chat_failures_total {chat_failures}",
                "# HELP british_museum_chat_no_evidence_total Chat responses without retrieved sources.",
                "# TYPE british_museum_chat_no_evidence_total counter",
                f"british_museum_chat_no_evidence_total {chat_no_evidence}",
                "# HELP british_museum_chat_tool_errors_total Tool calls whose status was not success.",
                "# TYPE british_museum_chat_tool_errors_total counter",
            ]
        )
        for tool_name, value in sorted(chat_tool_errors.items()):
            lines.append(
                "british_museum_chat_tool_errors_total"
                f"{_labels(tool=tool_name)} {value}"
            )

        lines.extend(
            [
                "# HELP british_museum_chat_confidence_ratio Chat response confidence.",
                "# TYPE british_museum_chat_confidence_ratio histogram",
                *_render_histogram(
                    "british_museum_chat_confidence_ratio",
                    chat_confidence,
                    {},
                ),
                "# HELP british_museum_chat_groundedness_proxy_ratio Retrieval-support proxy when sources exist.",
                "# TYPE british_museum_chat_groundedness_proxy_ratio histogram",
                *_render_histogram(
                    "british_museum_chat_groundedness_proxy_ratio",
                    chat_groundedness,
                    {},
                ),
                "# HELP british_museum_chat_hallucination_risk_proxy_ratio One minus the groundedness proxy.",
                "# TYPE british_museum_chat_hallucination_risk_proxy_ratio histogram",
                *_render_histogram(
                    "british_museum_chat_hallucination_risk_proxy_ratio",
                    chat_hallucination_risk,
                    {},
                ),
            ]
        )

        process = _process_metrics()
        lines.extend(
            [
                "# HELP process_cpu_seconds_total Total user and system CPU time spent in seconds.",
                "# TYPE process_cpu_seconds_total counter",
                f"process_cpu_seconds_total {_format_number(process['cpu_seconds_total'])}",
            ]
        )
        if process["resident_memory_bytes"] is not None:
            lines.extend(
                [
                    "# HELP process_resident_memory_bytes Resident memory size in bytes.",
                    "# TYPE process_resident_memory_bytes gauge",
                    f"process_resident_memory_bytes {process['resident_memory_bytes']}",
                ]
            )
        return "\n".join(lines) + "\n"


@lru_cache
def get_service_metrics() -> ServiceMetrics:
    return ServiceMetrics()


def reset_service_metrics() -> ServiceMetrics:
    """Replace the process registry; intended for deterministic tests."""
    get_service_metrics.cache_clear()
    return get_service_metrics()


def _merge_distributions(
    values: Any,
    buckets: tuple[float, ...],
) -> _Distribution:
    merged = _Distribution(buckets)
    for value in values:
        merged.count += value.count
        merged.total += value.total
        merged.maximum = max(merged.maximum, value.maximum)
        for index, count in enumerate(value.bucket_counts):
            merged.bucket_counts[index] += count
    return merged


def _copy_distribution(value: _Distribution) -> _Distribution:
    copied = _Distribution(value.buckets)
    copied.count = value.count
    copied.total = value.total
    copied.maximum = value.maximum
    copied.bucket_counts = list(value.bucket_counts)
    return copied


def _render_histogram(
    name: str,
    distribution: _Distribution,
    labels: dict[str, str],
) -> list[str]:
    lines: list[str] = []
    cumulative = 0
    for bucket, count in zip(distribution.buckets, distribution.bucket_counts):
        cumulative += count
        lines.append(
            f"{name}_bucket"
            f"{_labels(**labels, le=_format_number(bucket))} {cumulative}"
        )
    lines.append(f"{name}_bucket{_labels(**labels, le='+Inf')} {distribution.count}")
    lines.append(f"{name}_sum{_labels(**labels)} {_format_number(distribution.total)}")
    lines.append(f"{name}_count{_labels(**labels)} {distribution.count}")
    return lines


def _labels(**labels: str) -> str:
    if not labels:
        return ""
    rendered = ",".join(
        f'{name}="{_escape_label(str(value))}"' for name, value in sorted(labels.items())
    )
    return "{" + rendered + "}"


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_number(value: float) -> str:
    return format(float(value), ".12g")


def _normalize_method(method: str) -> str:
    normalized = str(method).upper()
    return normalized if normalized in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"} else "OTHER"


def _normalize_route(route: str | None) -> str:
    if not route or not route.startswith("/") or len(route) > 160:
        return "unmatched"
    return route.replace("\n", "").replace("\r", "")


def _normalize_tool_name(name: Any) -> str:
    normalized = _SAFE_TOOL_NAME.sub("_", str(name))[:64].strip("_")
    return normalized or "unknown"


def _process_metrics() -> dict[str, float | int | None]:
    return {
        "cpu_seconds_total": process_time(),
        "resident_memory_bytes": _resident_memory_bytes(),
    }


def _resident_memory_bytes() -> int | None:
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        with open("/proc/self/statm", encoding="ascii") as statm:
            resident_pages = int(statm.read().split()[1])
        return resident_pages * int(page_size)
    except (AttributeError, IndexError, OSError, TypeError, ValueError):
        pass

    try:
        import resource

        max_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return max_rss if sys.platform == "darwin" else max_rss * 1024
    except (ImportError, OSError, ValueError):
        return None
