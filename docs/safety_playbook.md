# RACS Operational Safety Playbook

## Purpose

This playbook defines the decision policies that RACS executes in response to operational events. Each policy is codified as executable logic in the corresponding Python module. Human operators can override any automated decision at any time.

---

## Degradation Policies

### DG-1: Automatic Degradation Trigger

**Condition:** Site composite risk score ≥ 0.75  
**Action:** Transition to `REDUCED_SPEED` (60% rated speed)  
**Module:** `racs/safety/controlled_degradation.py`  
**Override:** Operator can suppress via HumanOverrideSystem

### DG-2: Emergency Stop Trigger

**Condition:** Site composite risk score ≥ 0.95  
**Action:** Immediate transition to `EMERGENCY_STOP`  
**Module:** `racs/safety/controlled_degradation.py`  
**Override:** Requires two-operator confirmation to lift

### DG-3: Network Isolation Degradation

**Condition:** NetworkBrain heartbeat lost for > 30 seconds  
**Action:** Transition to `REDUCED_SPEED`, continue autonomous operation  
**Rationale:** This is the Brookings finding that 60%+ of robot accidents come from "unexpected activation" — RACS degrades predictably rather than continuing at full speed without oversight.

---

## Cascade Response Policies

### CR-1: Early Cascade Detection

**Condition:** ≥ 2 sites at HIGH or CRITICAL, cascade probability ≥ 0.65  
**Action:** NetworkBrain issues `throttle_outflow` to origin site; `reduce_intake` to at-risk peer sites  
**Module:** `racs/agents/network_brain.py`

### CR-2: Load Rebalancing

**Condition:** Any site utilisation > 80% while a peer is < 40%  
**Action:** `reduce_intake` on overloaded site; task redistribution to underloaded peer  
**Constraint:** Target site must be below 0.6 risk threshold to receive work

---

## Hard Safety Constraints

These cannot be modified by any software command — only by a YAML configuration change deployed by a qualified operator.

| Constraint | Default | Description |
|------------|---------|-------------|
| `max_robot_speed_ms` | 2.0 m/s | Absolute speed ceiling for any robot |
| `min_robot_spacing_m` | 1.5 m | Minimum distance between any two robots |
| `max_site_robot_density` | 85% | Maximum fraction of rated robot capacity |
| `max_fault_ratio` | 20% | Maximum fraction of faulted robots before site is quarantined |

---

## Human Override Workflow

1. Any action with `risk_score ≥ 0.75` generates an `OverrideRequest`
2. Request is logged and operator is notified (configurable callback)
3. Operator has up to 5 minutes to `approve()` or `reject()`
4. If no decision within 5 minutes, the request times out and the action is NOT executed
5. All decisions — including timeouts — are written to the audit log with operator ID and justification

---

## Post-Incident Review

Every safety and coordination event is logged to the `AuditLog`. After any incident, operators should:

1. Export the audit log: `audit.to_json()`
2. Filter events by time window and site: `audit.events_for_site("SITE_A")`
3. Review the degradation history: `DegradationController.history()`
4. Check for safety violations: `audit.events_of_type(AuditEventType.SAFETY_BLOCKED)`
