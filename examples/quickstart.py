"""
Quickstart: minimal working example of RACS risk prediction and safety evaluation.

Run: python examples/quickstart.py
"""

from racs.risk.predictor import RiskPredictor
from racs.risk.risk_signals import TelemetryInput
from racs.safety.constraints import SafetyConfig, SafetyGate

# 1. Describe what's happening at your facility right now
telemetry = TelemetryInput(
    site_id="WAREHOUSE_1",
    queue_length=45,
    robot_active_count=12,
    robot_fault_count=2,
    throughput_rate=0.73,
    error_rate_5min=0.08,
)

# 2. Predict risk from that telemetry
predictor = RiskPredictor()
signal = predictor.predict(telemetry)

print("=" * 50)
print("RACS Risk Signal")
print("=" * 50)
print(f"  Site:                {signal.site_id}")
print(f"  Risk Level:          {signal.level.value}")
print(f"  Congestion Prob:     {signal.congestion_probability:.2f}")
print(f"  Failure Likelihood:  {signal.failure_likelihood:.2f}")
print(f"  Recovery Latency:    {signal.recovery_latency_seconds:.1f}s")
print(f"  Composite Score:     {signal.composite_score:.3f}")
print(f"  Confidence:          {signal.confidence:.0%}")

# 3. Evaluate a proposed coordination action through the safety gate
gate = SafetyGate(SafetyConfig.default())

proposed_action = {
    "type": "redistribute_tasks",
    "target_site_id": "WAREHOUSE_2",
    "target_site_robot_density": 0.70,  # within safe limits
    "target_fault_ratio": 0.05,
    "risk_score": signal.composite_score,
}

result = gate.evaluate(proposed_action)
print("\n" + "=" * 50)
print("Safety Gate Evaluation")
print("=" * 50)
print(f"  Action:   {proposed_action['type']}")
print(f"  Allowed:  {result.allowed}")
print(f"  Decision: {result.action.value}")
print(f"  Reason:   {result.reason}")

# 4. Try an unsafe action
unsafe_action = {
    "type": "adjust_speed",
    "robot_speed_ms": 4.5,   # exceeds 2.0 m/s limit — will be blocked
    "target_site_robot_density": 0.60,
    "target_fault_ratio": 0.05,
    "risk_score": 0.3,
}

blocked_result = gate.evaluate(unsafe_action)
print("\n" + "=" * 50)
print("Unsafe Action (should be BLOCKED)")
print("=" * 50)
print(f"  Allowed:  {blocked_result.allowed}")
print(f"  Decision: {blocked_result.action.value}")
print(f"  Reason:   {blocked_result.reason}")
