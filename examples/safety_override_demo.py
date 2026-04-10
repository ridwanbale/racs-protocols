"""
Human-in-the-loop override demonstration.

Shows how high-risk actions are escalated to a human operator rather than
executed autonomously. The operator approves or rejects, and the full
decision chain is captured in the audit log.

Run: python examples/safety_override_demo.py
"""

from racs.safety.audit_log import AuditLog
from racs.safety.human_override import HumanOverrideSystem, OverrideDecision

audit = AuditLog()
override_system = HumanOverrideSystem(
    audit_log=audit,
    approval_timeout_s=300.0,
    on_escalation=lambda req: print(f"\n  [ESCALATION] Operator attention required at {req.site_id}"),
)

print("=" * 60)
print("Human-in-the-Loop Override Demo")
print("=" * 60)

# A high-risk action is proposed
request = override_system.request_approval(
    site_id="SITE_B",
    action_description="Redistribute 25% of SITE_B's task queue to SITE_C during CRITICAL risk state",
    risk_score=0.88,
    recommended_action="reduce_intake",
)

print(f"\nRequest ID:   {request.request_id}")
print(f"Site:         {request.site_id}")
print(f"Risk Score:   {request.risk_score:.0%}")
print(f"Description:  {request.action_description}")
print(f"Status:       {request.decision.value}")
print(f"\nPending requests: {len(override_system.pending_requests())}")

# Operator reviews and approves
print("\n[Operator OPS-007 reviews the request and approves...]")
approved = override_system.approve(
    request_id=request.request_id,
    operator_id="OPS-007",
    justification="SITE_C has 45% spare capacity and LOW risk — transfer is safe.",
)

print(f"\nDecision:      {approved.decision.value}")
print(f"Operator:      {approved.operator_id}")
print(f"Justification: {approved.operator_justification}")
print(f"Response time: {approved.decided_at - approved.timestamp:.1f}s")

# A second request — this one gets rejected
request2 = override_system.request_approval(
    site_id="SITE_A",
    action_description="Increase robot speed to 3.5 m/s to recover throughput",
    risk_score=0.79,
    recommended_action="adjust_speed",
)

print("\n[Operator OPS-007 rejects the speed increase as too risky...]")
rejected = override_system.reject(
    request_id=request2.request_id,
    operator_id="OPS-007",
    justification="Speed increase during HIGH risk state violates site safety policy. Use staged recovery instead.",
)

print(f"\nDecision:      {rejected.decision.value}")
print(f"Justification: {rejected.operator_justification}")

# Show audit trail
print("\n" + "=" * 60)
print("Audit Trail")
print("=" * 60)
for event in audit.recent(10):
    print(f"  [{event.event_type.value:25s}] {event.site_id}: {event.description[:60]}")

print(f"\nAvg operator response latency: {override_system.avg_response_latency():.1f}s")
