"""Cross-site Network Brain — subscribes to all site signals and issues coordination commands."""

from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional

from ..coordination.load_balancer import LoadBalancer, RebalanceCommand
from ..coordination.recovery_planner import RecoveryPlan, RecoveryPlanner
from ..coordination.task_allocator import AllocationResult, Task, TaskAllocator
from ..risk.cascade_detector import CascadeAlert, CascadeDetector
from ..risk.risk_aggregator import RiskAggregator
from ..risk.risk_signals import RiskLevel, RiskSignal
from ..safety.audit_log import AuditEventType, AuditLog
from ..safety.constraints import SafetyConfig, SafetyGate


RISK_LEVEL_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


class NetworkBrain:
    """
    Central coordination layer. Receives risk signals from all site agents,
    detects cascade risk, and issues safety-gated coordination commands.

    Has no single point of failure: if the NetworkBrain goes down, each
    SiteAgent continues operating autonomously in local-safe mode.
    """

    def __init__(
        self,
        safety_config: Optional[SafetyConfig] = None,
        on_command: Optional[Callable[[str, dict], None]] = None,
        audit_log_path: Optional[str] = None,
        intervention_risk_level: RiskLevel = RiskLevel.MEDIUM,
        robot_anomaly_threshold: float = 1.0,
    ) -> None:
        self._aggregator = RiskAggregator()
        self._cascade_detector = CascadeDetector()
        self._load_balancer = LoadBalancer()
        self._task_allocator = TaskAllocator()
        self._recovery_planner = RecoveryPlanner()
        self._safety_gate = SafetyGate(safety_config)
        self._audit = AuditLog(log_path=audit_log_path)
        self._on_command = on_command  # callback: (site_id, command_dict) -> None
        self._active_recovery_plans: Dict[str, RecoveryPlan] = {}
        self._site_utilisation: Dict[str, float] = {}
        self._local_interventions: Dict[str, dict] = {}
        self._intervention_risk_level = intervention_risk_level
        self._robot_anomaly_threshold = robot_anomaly_threshold

    def ingest_signal(
        self, signal: RiskSignal, now: Optional[float] = None
    ) -> Optional[CascadeAlert]:
        """
        Process an incoming risk signal from a site agent.
        Returns a CascadeAlert if a cascade is detected.
        """
        effective_now = time.time() if now is None else now
        self._aggregator.update(signal)
        self._cascade_detector.ingest(signal, now=effective_now)

        self._audit.log(
            AuditEventType.RISK_SIGNAL,
            site_id=signal.site_id,
            description=f"{signal.site_id}: {signal.level.value} (score={signal.composite_score:.2f})",
            details=signal.to_dict(),
        )

        current = self._aggregator.all_current(now=effective_now)
        alert = self._cascade_detector.detect(current)

        if alert:
            self._audit.log(
                AuditEventType.CASCADE_ALERT,
                site_id=alert.origin_site,
                description=alert.description,
                details=alert.to_dict(),
            )
            self._respond_to_cascade(alert)

        self._respond_to_local_risk(signal)
        self._rebalance_if_needed(now=effective_now)
        return alert

    def _respond_to_local_risk(self, signal: RiskSignal) -> None:
        if signal.site_id in self._local_interventions:
            return
        if RISK_LEVEL_ORDER[signal.level] < RISK_LEVEL_ORDER[self._intervention_risk_level]:
            return
        telemetry = signal.source_telemetry
        suspect_robot_id = getattr(telemetry, "suspect_robot_id", None)
        if not suspect_robot_id:
            return
        robot_anomaly = getattr(telemetry, "suspect_robot_anomaly", 0.0)
        if robot_anomaly < self._robot_anomaly_threshold:
            return

        command = {
            "type": "drain_robot",
            "site_id": signal.site_id,
            "robot_id": suspect_robot_id,
            "risk_score": signal.composite_score,
            "risk_level": signal.level.value,
            "robot_anomaly": robot_anomaly,
            "rationale": "local risk signal exceeded predictive drain threshold",
        }
        self._local_interventions[signal.site_id] = command
        self._issue_command(signal.site_id, command)

    def _respond_to_cascade(self, alert: CascadeAlert) -> None:
        if alert.origin_site in self._active_recovery_plans:
            return  # already handling this site

        plan = self._recovery_planner.plan_cascade_containment(
            alert.origin_site, alert.affected_sites
        )
        self._active_recovery_plans[alert.origin_site] = plan

        command = {
            "type": "throttle_outflow",
            "site_id": alert.origin_site,
            "target_throughput_fraction": 0.5,
            "risk_score": alert.cascade_probability,
        }
        self._issue_command(alert.origin_site, command)

    def _rebalance_if_needed(self, now: Optional[float] = None) -> None:
        if not self._site_utilisation:
            return
        current_signals = self._aggregator.all_current(now=now)
        risk_scores = {sid: sig.composite_score for sid, sig in current_signals.items()}

        # Don't send work toward high-risk sites
        excluded = [
            sid for sid, sig in current_signals.items()
            if sig.level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        ]
        commands = self._load_balancer.compute_rebalance(
            self._site_utilisation, risk_scores, excluded_targets=excluded
        )
        for cmd in commands:
            self._issue_command(cmd.from_site, {
                "type": "reduce_intake",
                "transfer_fraction": cmd.transfer_fraction,
                "recipient_site": cmd.to_site,
                "rationale": cmd.rationale,
                "risk_score": risk_scores.get(cmd.from_site, 0),
            })

    def _issue_command(self, site_id: str, command: dict) -> None:
        result = self._safety_gate.evaluate(command)
        if result.allowed:
            self._audit.log(
                AuditEventType.COORDINATION_COMMAND,
                site_id=site_id,
                description=f"NetworkBrain issued: {command.get('type')}",
                details=command,
            )
            if self._on_command:
                self._on_command(site_id, command)
        else:
            self._audit.log(
                AuditEventType.SAFETY_BLOCKED,
                site_id=site_id,
                description=f"NetworkBrain command blocked: {result.reason}",
                details=result.to_dict(),
            )

    def update_utilisation(self, site_id: str, utilisation: float) -> None:
        self._site_utilisation[site_id] = utilisation

    def tick_recovery_plans(self) -> None:
        """Advance all active recovery plans that are ready."""
        for site_id, plan in list(self._active_recovery_plans.items()):
            plan.advance()
            if plan.is_complete:
                del self._active_recovery_plans[site_id]

    def network_summary(self) -> dict:
        return {
            "timestamp": time.time(),
            "aggregator": self._aggregator.summary(),
            "active_recovery_plans": list(self._active_recovery_plans.keys()),
            "balance_score": self._load_balancer.network_balance_score(self._site_utilisation),
            "total_audit_events": len(self._audit),
        }
