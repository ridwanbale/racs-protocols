# RACS Protocol Specification v1.0.0

## Overview

The RACS Protocol defines the message formats, exchange sequences, and behavioral contracts that enable heterogeneous autonomous systems to share risk signals and coordinate responses without exposing proprietary implementation details.

All messages are JSON objects validated against the JSON Schema definitions in `racs/protocols/schemas/`.

---

## Protocol Layers

### Layer 1 — Risk Signal Exchange

Site Agents publish `RiskSignal` messages on a defined interval (default: 10 seconds). These signals encode predictive assessments — not just current state — enabling peers to anticipate failures before they propagate.

**Topic:** `racs.risk.signals`  
**Schema:** `risk_signal.schema.json`

**Exchange sequence:**
```
SiteAgent → [publishes RiskSignal every 10s] → Kafka topic / in-memory bus
NetworkBrain → [subscribes, ingests all signals]
CascadeDetector → [evaluates cross-site risk]
```

### Layer 2 — Coordination Commands

The NetworkBrain issues `CoordinationCommand` messages to specific Site Agents when load imbalance or cascade risk is detected.

**Topic:** `racs.coordination.commands`  
**Schema:** `coordination_command.schema.json`

Commands must pass through the target agent's `SafetyGate` before execution. If the gate blocks the command, the agent logs a `SAFETY_BLOCKED` audit event and does not execute.

**Command types:**

| Type | Description |
|------|-------------|
| `throttle_outflow` | Reduce task output rate from a congested site |
| `reduce_intake` | Reject new task assignments temporarily |
| `redistribute_tasks` | Move queued tasks to a specified peer site |
| `emergency_stop` | Halt all robot motion immediately |
| `safe_hold` | Pause non-essential operations |
| `resume_normal` | Return to standard operating mode |
| `adjust_speed` | Change robot speed (subject to hard limits) |

### Layer 3 — Agent Status / Heartbeat

Site Agents publish status heartbeats every 15 seconds.

**Topic:** `racs.agents.status`  
**Schema:** `agent_status.schema.json`

If the NetworkBrain does not receive a heartbeat within 30 seconds, it marks the agent as OFFLINE and adjusts cross-site routing.

### Layer 4 — Safety Override Events

Human override decisions are published for audit and compliance.

**Topic:** `racs.safety.overrides`  
**Schema:** `safety_override.schema.json`

---

## Safety Contract

All protocol participants **MUST** enforce these invariants:

1. **Hard constraints are non-negotiable.** No message, however authorised, may instruct a robot to exceed its maximum rated speed or enter an exclusion zone.
2. **Unknown commands are rejected.** A Site Agent that receives a command type it does not recognise must reject it and log a `SAFETY_BLOCKED` event.
3. **Stale signals are ignored.** Risk signals older than 120 seconds are treated as absent; the agent reverts to autonomous conservative operation.
4. **Human approval for high-risk actions.** Any command where `requires_human_approval: true` must not execute until an operator decision is recorded.

---

## Versioning

The protocol version is carried in the `$id` field of each JSON Schema. Breaking changes increment the major version. Agents MUST reject messages from an incompatible major version.
