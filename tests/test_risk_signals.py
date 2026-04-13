"""Tests for risk signal definitions and the risk predictor."""

import time

import pytest

from racs.risk.predictor import RiskPredictor
from racs.risk.risk_signals import RiskLevel, RiskSignal, TelemetryInput


def make_telemetry(**overrides) -> TelemetryInput:
    defaults = dict(
        site_id="TEST_SITE",
        queue_length=10,
        robot_active_count=20,
        robot_fault_count=0,
        throughput_rate=0.90,
        error_rate_5min=0.01,
    )
    defaults.update(overrides)
    return TelemetryInput(**defaults)


class TestRiskLevel:
    def test_from_score_boundaries(self):
        assert RiskLevel.from_score(0.0) == RiskLevel.LOW
        assert RiskLevel.from_score(0.29) == RiskLevel.LOW
        assert RiskLevel.from_score(0.30) == RiskLevel.MEDIUM
        assert RiskLevel.from_score(0.54) == RiskLevel.MEDIUM
        assert RiskLevel.from_score(0.55) == RiskLevel.HIGH
        assert RiskLevel.from_score(0.79) == RiskLevel.HIGH
        assert RiskLevel.from_score(0.80) == RiskLevel.CRITICAL
        assert RiskLevel.from_score(1.00) == RiskLevel.CRITICAL


class TestRiskSignal:
    def test_composite_score_range(self):
        sig = RiskSignal(
            site_id="A",
            congestion_probability=0.5,
            failure_likelihood=0.5,
            recovery_latency_seconds=60,
            level=RiskLevel.MEDIUM,
        )
        assert 0.0 <= sig.composite_score <= 1.0

    def test_composite_score_high_when_severe(self):
        sig = RiskSignal(
            site_id="A",
            congestion_probability=0.95,
            failure_likelihood=0.90,
            recovery_latency_seconds=300,
            level=RiskLevel.CRITICAL,
        )
        assert sig.composite_score > 0.80

    def test_composite_score_low_when_healthy(self):
        sig = RiskSignal(
            site_id="A",
            congestion_probability=0.05,
            failure_likelihood=0.05,
            recovery_latency_seconds=10,
            level=RiskLevel.LOW,
        )
        assert sig.composite_score < 0.20

    def test_to_dict_and_from_dict_roundtrip(self):
        sig = RiskSignal(
            site_id="SITE_X",
            congestion_probability=0.42,
            failure_likelihood=0.33,
            recovery_latency_seconds=55.0,
            level=RiskLevel.MEDIUM,
            confidence=0.85,
        )
        d = sig.to_dict()
        restored = RiskSignal.from_dict(d)
        assert restored.site_id == sig.site_id
        assert abs(restored.congestion_probability - sig.congestion_probability) < 1e-9
        assert restored.level == sig.level


class TestRiskPredictor:
    def test_predict_returns_risk_signal(self):
        predictor = RiskPredictor()
        t = make_telemetry()
        sig = predictor.predict(t)
        assert isinstance(sig, RiskSignal)
        assert sig.site_id == "TEST_SITE"

    def test_healthy_facility_is_low_risk(self):
        predictor = RiskPredictor()
        t = make_telemetry(queue_length=5, robot_fault_count=0, throughput_rate=0.95, error_rate_5min=0.0)
        sig = predictor.predict(t)
        assert sig.level in (RiskLevel.LOW, RiskLevel.MEDIUM)

    def test_stressed_facility_is_high_risk(self):
        predictor = RiskPredictor()
        t = make_telemetry(
            queue_length=90, robot_active_count=8, robot_fault_count=8,
            throughput_rate=0.20, error_rate_5min=0.40,
        )
        sig = predictor.predict(t)
        # Stressed facility must be at least MEDIUM and score above healthy baseline
        assert sig.level in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)
        assert sig.composite_score > 0.35

    def test_probabilities_bounded(self):
        predictor = RiskPredictor()
        for _ in range(20):
            import random
            t = make_telemetry(
                queue_length=random.randint(0, 100),
                robot_fault_count=random.randint(0, 10),
                throughput_rate=random.uniform(0.1, 1.0),
                error_rate_5min=random.uniform(0.0, 0.5),
            )
            sig = predictor.predict(t)
            assert 0.0 <= sig.congestion_probability <= 1.0
            assert 0.0 <= sig.failure_likelihood <= 1.0
            assert sig.recovery_latency_seconds >= 0.0
