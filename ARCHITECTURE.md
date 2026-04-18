# RACS System Architecture

## Overview

RACS uses a two-layer architecture that provides both real-time coordination and decentralised resilience.

```
┌─────────────────────────────────────────────────────────────────┐
│                        LAYER 2: NETWORK BRAIN                   │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │ Risk         │  │ Cascade          │  │ Load             │  │
│  │ Aggregator   │  │ Detector         │  │ Balancer         │  │
│  └──────┬───────┘  └────────┬─────────┘  └────────┬─────────┘  │
│         │                   │                      │            │
│  ┌──────▼───────────────────▼──────────────────────▼─────────┐  │
│  │              Coordination Engine (+ SafetyGate)            │  │
│  └────────────────────────────────────────────────────────────┘  │
│         ↑ RiskSignals                  ↓ CoordinationCommands    │
└─────────┼──────────────────────────────┼───────────────────────┘
          │                              │
┌─────────┼──────────────────────────────┼───────────────────────┐
│         │      LAYER 1: SITE AGENTS    │                        │
│  ┌──────┴─────────────────────────────┴──────┐                  │
│  │                 SITE AGENT                 │                  │
│  │                                            │                  │
│  │  Telemetry → Risk Predictor → RiskSignal   │                  │
│  │                    ↓                       │                  │
│  │           Degradation Controller           │                  │
│  │                    ↓                       │                  │
│  │        SafetyGate → Execute/Block/Escalate │                  │
│  │                    ↓                       │                  │
│  │         Human Override System              │                  │
│  │                    ↓                       │                  │
│  │              Audit Log                     │                  │
│  └────────────────────────────────────────────┘                  │
└─────────────────────────────────────────────────────────────────┘
```

## Data Flow

```
Facility Telemetry
    │
    ▼
TelemetryInput (queue_length, robot_counts, throughput_rate, error_rate)
    │
    ▼
RiskPredictor (XGBoost / heuristic fallback)
    │
    ▼
RiskSignal (congestion_prob, failure_likelihood, recovery_latency, level)
    │
    ├──► Published to NetworkBrain (via Kafka or direct callback)
    │
    ▼
DegradationController
    │ score ≥ 0.75 → REDUCED_SPEED
    │ score ≥ 0.95 → EMERGENCY_STOP
    │
    ▼
Incoming CoordinationCommand from NetworkBrain
    │
    ▼
SafetyGate.evaluate(command)
    │
    ├── ALLOW          → Execute immediately
    ├── ESCALATE       → HumanOverrideSystem.request_approval()
    └── BLOCK          → Log SAFETY_BLOCKED, discard command
```

## Safety Constraint Hierarchy

```
┌─────────────────────────────────────────────────┐
│  HARD CONSTRAINTS (cannot be overridden by code) │
│  • max_robot_speed_ms                           │
│  • max_site_robot_density                       │
│  • max_fault_ratio                              │
│  • exclusion_zones                              │
│  • emergency_stop_risk_threshold                │
├─────────────────────────────────────────────────┤
│  SOFT CONSTRAINTS (human can authorise)         │
│  • escalate_risk_threshold                      │
├─────────────────────────────────────────────────┤
│  OPTIMISATION (load balancing, task allocation) │
│  • operates only within constraint envelope     │
└─────────────────────────────────────────────────┘
```

## Decentralised Resilience

If the Network Brain becomes unreachable:

```
NetworkBrain OFFLINE
    │
    ▼
SiteAgent.mark_disconnected()
    │
    ▼
DegradationController: enter REDUCED_SPEED
    │
    ▼
Continue autonomous operation:
  - Risk prediction continues locally
  - Safety constraints enforced locally
  - Human override available locally
  - No cross-site coordination (conservative)
    │
    ▼
NetworkBrain reconnects → mark_connected() → attempt_recovery()
```

## Module Dependency Map

```
racs/
├── risk/
│   ├── risk_signals.py          (core data types — no dependencies)
│   ├── predictor.py             (depends: risk_signals)
│   ├── cascade_detector.py      (depends: risk_signals)
│   └── risk_aggregator.py       (depends: risk_signals)
│
├── safety/
│   ├── audit_log.py             (no racs dependencies)
│   ├── constraints.py           (no racs dependencies)
│   ├── human_override.py        (depends: audit_log)
│   └── controlled_degradation.py (depends: audit_log)
│
├── coordination/
│   ├── task_allocator.py        (no racs dependencies)
│   ├── load_balancer.py         (no racs dependencies)
│   ├── recovery_planner.py      (no racs dependencies)
│   └── consensus.py             (no racs dependencies)
│
├── agents/
│   ├── agent_registry.py        (no racs dependencies)
│   ├── site_agent.py            (depends: risk/*, safety/*)
│   └── network_brain.py         (depends: risk/*, coordination/*, safety/*)
│
└── protocols/
    ├── kafka_events.py          (depends: risk/risk_signals)
    ├── ros2_interfaces.py       (depends: risk/risk_signals, safety/constraints)
    └── schemas/                 (JSON Schema — no Python dependencies)
```
