"""Provider-neutral operational metrics.

A small in-process registry of counters, gauges, and duration histograms,
rendered in the Prometheus text format so Grafana can chart it, plus a helper
that opens a Sentry span when error reporting is enabled.

Privacy rule: a metric name and its labels describe *what kind* of thing
happened, never *which* thing. Labels are restricted to short identifier-shaped
values drawn from fixed vocabularies (origin, outcome, operation, stage), so a
workspace id, project id, record id, entity name, or any story text can never
become a label. Values are numbers only.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_LABEL_VALUE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_METRIC_NAME = re.compile(r"^[a-z][a-z0-9_]{2,79}$")
_BUCKETS: tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

LabelKey = tuple[tuple[str, str], ...]


class MetricLabelError(ValueError):
    """A label value was not a short identifier (it might have carried an id or content)."""


def _key(labels: dict[str, str]) -> LabelKey:
    for name, value in labels.items():
        if not isinstance(value, str) or not _LABEL_VALUE.match(value):
            raise MetricLabelError(f"metric label {name!r} must be a short lowercase identifier")
    return tuple(sorted(labels.items()))


def _format(labels: LabelKey, extra: tuple[tuple[str, str], ...] = ()) -> str:
    pairs = [*labels, *extra]
    if not pairs:
        return ""
    return "{" + ",".join(f'{name}="{value}"' for name, value in pairs) + "}"


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._help: dict[str, tuple[str, str]] = {}
        self._counters: dict[str, dict[LabelKey, float]] = {}
        self._gauges: dict[str, dict[LabelKey, float]] = {}
        self._histograms: dict[str, dict[LabelKey, tuple[list[int], float, int]]] = {}

    def _declare(self, name: str, kind: str, description: str) -> None:
        if not _METRIC_NAME.match(name):
            raise ValueError(f"invalid metric name {name!r}")
        existing = self._help.setdefault(name, (kind, description))
        if existing[0] != kind:
            raise ValueError(f"metric {name!r} is already a {existing[0]}")

    def increment(self, name: str, description: str, amount: float = 1.0, **labels: str) -> None:
        key = _key(labels)
        with self._lock:
            self._declare(name, "counter", description)
            series = self._counters.setdefault(name, {})
            series[key] = series.get(key, 0.0) + amount

    def set_gauge(self, name: str, description: str, value: float, **labels: str) -> None:
        key = _key(labels)
        with self._lock:
            self._declare(name, "gauge", description)
            self._gauges.setdefault(name, {})[key] = float(value)

    def observe(self, name: str, description: str, seconds: float, **labels: str) -> None:
        key = _key(labels)
        with self._lock:
            self._declare(name, "histogram", description)
            series = self._histograms.setdefault(name, {})
            buckets, total, count = series.get(key, ([0] * len(_BUCKETS), 0.0, 0))
            buckets = list(buckets)
            for index, bound in enumerate(_BUCKETS):
                if seconds <= bound:
                    buckets[index] += 1
            series[key] = (buckets, total + seconds, count + 1)

    def value(self, name: str, **labels: str) -> float:
        """Current counter or gauge value (0 when the series does not exist)."""
        key = _key(labels)
        with self._lock:
            for store in (self._counters, self._gauges):
                if name in store:
                    return store[name].get(key, 0.0)
        return 0.0

    def count(self, name: str, **labels: str) -> int:
        """Number of observations recorded in a histogram series."""
        key = _key(labels)
        with self._lock:
            return self._histograms.get(name, {}).get(key, ([], 0.0, 0))[2]

    def reset(self) -> None:
        with self._lock:
            self._help.clear()
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()

    def render(self) -> str:
        """Prometheus text exposition format (version 0.0.4)."""
        lines: list[str] = []
        with self._lock:
            for name in sorted(self._help):
                kind, description = self._help[name]
                lines.append(f"# HELP {name} {description}")
                lines.append(f"# TYPE {name} {kind}")
                if kind == "histogram":
                    for key, (buckets, total, count) in sorted(self._histograms[name].items()):
                        for bound, hits in zip(_BUCKETS, buckets, strict=True):
                            lines.append(
                                f"{name}_bucket{_format(key, (('le', repr(bound)),))} {hits}"
                            )
                        lines.append(f"{name}_bucket{_format(key, (('le', '+Inf'),))} {count}")
                        lines.append(f"{name}_sum{_format(key)} {total:.6f}")
                        lines.append(f"{name}_count{_format(key)} {count}")
                else:
                    store = self._counters if kind == "counter" else self._gauges
                    for key, value in sorted(store.get(name, {}).items()):
                        lines.append(f"{name}{_format(key)} {value:g}")
        return "\n".join(lines) + "\n"


metrics = MetricsRegistry()


@contextmanager
def span(operation: str, **tags: str) -> Iterator[None]:
    """Open a Sentry span for an operation when error reporting is active.

    Tags obey the same rule as metric labels. Nothing is imported or sent unless
    Sentry was initialized, so this is free in tests and unconfigured deployments.
    """
    _key(tags)
    from backend.core import observability

    if not observability._initialized:
        yield
        return
    import sentry_sdk

    with sentry_sdk.start_span(op=operation, name=operation) as active:
        for name, value in tags.items():
            active.set_tag(name, value)
        yield


@contextmanager
def timed(name: str, description: str, **labels: str) -> Iterator[dict[str, Any]]:
    """Record a duration histogram; the yielded dict may add labels before exit."""
    extra: dict[str, Any] = {}
    started = time.perf_counter()
    try:
        yield extra
    finally:
        metrics.observe(name, description, time.perf_counter() - started, **labels, **extra)


__all__ = ["MetricLabelError", "MetricsRegistry", "metrics", "span", "timed"]
