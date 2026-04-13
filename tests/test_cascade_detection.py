"""Tests for cascade detection."""

import time

import pytest

from racs.risk.cascade_detector import CascadeDetector
from racs.risk.risk_signals import RiskLevel, RiskSignal


def make_signal(site_id: str, level: RiskLevel, score: float) -> RiskSignal:
    return RiskSignal(
        site_id=site_id,
        congestion_probability=score,
        failure_likelihood=score * 0.8,
        recovery_latency_seconds=score * 120,
        level=level,
    )


class TestCascadeDetector:
    def test_no_alert_below_threshold(self):
        detector = CascadeDetector(cascade_threshold=0.65, min_sites_for_cascade=2)
        signals = {
            "A": make_signal("A", RiskLevel.LOW, 0.1),
            "B": make_signal("B", RiskLevel.LOW, 0.1),
        }
        alert = detector.detect(signals)
        assert alert is None

    def test_alert_when_multiple_sites_high(self):
        detector = CascadeDetector(cascade_threshold=0.50, min_sites_for_cascade=2)
        signals = {
            "A": make_signal("A", RiskLevel.HIGH, 0.85),
            "B": make_signal("B", RiskLevel.HIGH, 0.80),
            "C": make_signal("C", RiskLevel.LOW, 0.15),
        }
        alert = detector.detect(signals)
        assert alert is not None
        assert alert.cascade_probability >= 0.50

    def test_alert_identifies_origin(self):
        detector = CascadeDetector(cascade_threshold=0.50)
        signals = {
            "A": make_signal("A", RiskLevel.CRITICAL, 0.95),
            "B": make_signal("B", RiskLevel.HIGH, 0.70),
        }
        alert = detector.detect(signals)
        assert alert is not None
        assert alert.origin_site == "A"

    def test_ingest_and_trend_positive(self):
        detector = CascadeDetector()
        for score in [0.1, 0.2, 0.3, 0.4, 0.5]:
            sig = make_signal("A", RiskLevel.from_score(score), score)
            sig.timestamp = time.time()
            detector.ingest(sig)
        trend = detector.trend_for_site("A")
        assert trend > 0.0

    def test_min_sites_threshold_respected(self):
        detector = CascadeDetector(cascade_threshold=0.50, min_sites_for_cascade=3)
        signals = {
            "A": make_signal("A", RiskLevel.CRITICAL, 0.95),
            "B": make_signal("B", RiskLevel.HIGH, 0.80),
        }
        # Only 2 sites elevated, threshold is 3
        alert = detector.detect(signals)
        assert alert is None
