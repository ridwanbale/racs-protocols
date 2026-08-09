"""Tests for deterministic simulated time across RACS simulation components."""

from __future__ import annotations

import time

import pytest

from racs.agents.network_brain import NetworkBrain
from racs.agents.site_agent import AgentConfig, SiteAgent
from racs.risk.cascade_detector import CascadeDetector
from racs.risk.risk_aggregator import RiskAggregator
from racs.risk.risk_signals import RiskLevel, RiskSignal, TelemetryInput
from simulations.warehouse_sim import SimSite, SimulationScenarioConfig, WarehouseSimulation


def make_telemetry(site_id: str = "SITE_A", timestamp: float = 0.0) -> TelemetryInput:
    return TelemetryInput(
        site_id=site_id,
        queue_length=10,
        robot_active_count=20,
        robot_fault_count=0,
        throughput_rate=0.90,
        error_rate_5min=0.01,
        timestamp=timestamp,
    )


def make_signal(site_id: str, timestamp: float, score: float = 0.2) -> RiskSignal:
    return RiskSignal(
        site_id=site_id,
        congestion_probability=score,
        failure_likelihood=score,
        recovery_latency_seconds=30.0,
        level=RiskLevel.from_score(score),
        timestamp=timestamp,
    )


def test_site_agent_publication_cadence_uses_explicit_simulated_time() -> None:
    published: list[RiskSignal] = []
    agent = SiteAgent(
        AgentConfig(site_id="SITE_A", risk_publish_interval_s=10.0),
        on_signal_publish=published.append,
    )

    assert agent.tick(make_telemetry(timestamp=0.0), now=0.0) is not None
    assert agent.tick(make_telemetry(timestamp=1.0), now=1.0) is None
    assert agent.tick(make_telemetry(timestamp=9.9), now=9.9) is None

    signal = agent.tick(make_telemetry(timestamp=10.0), now=10.0)

    assert signal is not None
    assert [sig.timestamp for sig in published] == [0.0, 10.0]


def test_repeated_fast_ticks_do_not_depend_on_elapsed_wall_clock_time() -> None:
    published: list[RiskSignal] = []
    agent = SiteAgent(
        AgentConfig(site_id="SITE_A", risk_publish_interval_s=10.0),
        on_signal_publish=published.append,
    )

    started = time.time()
    for simulated_time in [0.0, 1.0, 2.0, 3.0, 4.0]:
        agent.tick(make_telemetry(timestamp=simulated_time), now=simulated_time)

    assert time.time() - started < 10.0
    assert [sig.timestamp for sig in published] == [0.0]


def test_signal_publishes_once_simulated_time_crosses_interval() -> None:
    agent = SiteAgent(AgentConfig(site_id="SITE_A", risk_publish_interval_s=5.0))

    assert agent.tick(make_telemetry(timestamp=100.0), now=100.0) is not None
    assert agent.tick(make_telemetry(timestamp=104.99), now=104.99) is None
    assert agent.tick(make_telemetry(timestamp=105.0), now=105.0) is not None


def test_risk_signal_timestamp_matches_explicit_tick_time() -> None:
    agent = SiteAgent(AgentConfig(site_id="SITE_A", risk_publish_interval_s=10.0))

    signal = agent.tick(make_telemetry(timestamp=123.0), now=123.0)

    assert signal is not None
    assert signal.timestamp == 123.0
    assert signal.source_telemetry is not None
    assert signal.source_telemetry.timestamp == 123.0


def test_risk_aggregator_uses_explicit_simulated_now_for_freshness() -> None:
    aggregator = RiskAggregator(staleness_threshold_s=10.0)
    aggregator.update(make_signal("FRESH", timestamp=100.0))
    aggregator.update(make_signal("STALE", timestamp=95.0))

    assert set(aggregator.all_current(now=104.0)) == {"FRESH", "STALE"}
    assert set(aggregator.all_current(now=106.0)) == {"FRESH"}
    assert aggregator.stale_sites(now=106.0) == ["STALE"]


def test_cascade_detector_prunes_history_using_explicit_simulated_now() -> None:
    detector = CascadeDetector(signal_window_s=10.0)

    detector.ingest(make_signal("SITE_A", timestamp=100.0), now=100.0)
    detector.ingest(make_signal("SITE_A", timestamp=111.0), now=111.0)

    history = detector._signal_history["SITE_A"]
    assert [signal.timestamp for signal in history] == [111.0]


def test_sim_site_allows_explicit_telemetry_timestamp() -> None:
    site = SimSite(site_id="SITE_A")

    telemetry = site.to_telemetry(timestamp=42.0)

    assert telemetry.timestamp == 42.0


def test_warehouse_simulation_publishes_multiple_signals_with_simulated_time() -> None:
    config = SimulationScenarioConfig(
        steps=4,
        site_ids=("SITE_A",),
        seed=7,
        step_duration_s=10.0,
        fault_site="SITE_A",
        fault_step=0,
        fault_count=0,
    )
    sim = WarehouseSimulation(
        config=config,
        with_racs=True,
    )

    sim.run()

    history = sim._brain._cascade_detector._signal_history["SITE_A"]
    assert [signal.timestamp for signal in history] == [0.0, 10.0, 20.0, 30.0]


def test_warehouse_simulation_publication_timing_is_reproducible(monkeypatch) -> None:
    original_ingest = NetworkBrain.ingest_signal

    def published_timestamps() -> list[float]:
        timestamps: list[float] = []

        def capture_ingest(self, signal, now=None):
            timestamps.append(signal.timestamp)
            return original_ingest(self, signal, now=now)

        monkeypatch.setattr(NetworkBrain, "ingest_signal", capture_ingest)
        config = SimulationScenarioConfig(
            steps=4,
            site_ids=("SITE_A",),
            seed=7,
            step_duration_s=10.0,
            fault_site="SITE_A",
            fault_step=0,
            fault_count=0,
        )
        sim = WarehouseSimulation(
            config=config,
            with_racs=True,
        )
        sim.run()
        return timestamps

    assert published_timestamps() == published_timestamps()


def test_warehouse_simulation_rejects_zero_step_duration() -> None:
    with pytest.raises(ValueError, match="step_duration_s"):
        WarehouseSimulation(step_duration_s=0)


def test_warehouse_simulation_rejects_negative_step_duration() -> None:
    with pytest.raises(ValueError, match="step_duration_s"):
        WarehouseSimulation(step_duration_s=-1.0)


def test_warehouse_simulation_accepts_positive_fractional_step_duration() -> None:
    config = SimulationScenarioConfig(
        steps=1,
        site_ids=("SITE_A",),
        seed=7,
        step_duration_s=0.5,
        fault_site="SITE_A",
        fault_step=0,
        fault_count=0,
    )
    sim = WarehouseSimulation(
        config=config,
        with_racs=True,
    )

    assert sim.run()[0]["step"] == 0


def test_site_agent_default_wall_clock_behavior_remains_compatible(monkeypatch) -> None:
    published: list[RiskSignal] = []
    agent = SiteAgent(
        AgentConfig(site_id="SITE_A", risk_publish_interval_s=10.0),
        on_signal_publish=published.append,
    )
    current_time = iter([100.0, 105.0, 111.0])
    monkeypatch.setattr("racs.agents.site_agent.time.time", lambda: next(current_time))

    first = agent.tick(make_telemetry())
    second = agent.tick(make_telemetry())
    third = agent.tick(make_telemetry())

    assert first is not None
    assert second is None
    assert third is not None
    assert [signal.timestamp for signal in published] == [100.0, 111.0]
