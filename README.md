# RACS Protocols — Risk-Aware Coordination System for Autonomous Operations

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![NIST AI RMF Aligned](https://img.shields.io/badge/NIST_AI_RMF-Aligned-green.svg)](NIST_ALIGNMENT.md)

## The Problem

As U.S. logistics and manufacturing operations increasingly depend on autonomous systems — robotic material handling, automated guided vehicles, AI-driven control software — a critical gap has emerged: **individual robots and automation components work reliably in isolation, but fail to coordinate safely as systems.**

This coordination gap produces cascading failures where a localized disruption (a single robot fault, a lane backup, a software misconfiguration) propagates across an entire facility or network. The consequences are severe:

- **$1.4 trillion** in annual losses from unplanned downtime across Fortune 500 companies ([Siemens, 2024](https://www.siemens.com/true-cost-of-downtime))
- **50% higher injury rates** in warehouses with robots vs. without ([Brookings Institution, 2023](https://www.brookings.edu/articles/keeping-workers-safe-in-the-automation-revolution/))
- **$5.4 billion** in losses from a single cascading software failure (CrowdStrike incident, July 2024)

Federal agencies have identified this gap as a national priority:
- **DARPA** launched the [Resilient Supply-and-Demand Networks (RSDN)](https://www.darpa.mil/program/resilient-supply-and-demand-networks) program seeking "agent-based" solutions for supply chain resilience
- The **DOD-funded ARM Institute** issued [Technology Project Call 24-01](https://arminstitute.org/) requesting multi-robot coordination solutions, stating that **"no systems currently exist for dynamic, distributed sensing for safety"**
- **NIST** established the [U.S. AI Safety Institute](https://www.nist.gov/artificial-intelligence/ai-safety-institute) to ensure AI systems in critical infrastructure are robust against cascading failures

Yet no open, vendor-agnostic coordination framework exists for U.S. operators — particularly small and medium enterprises — to adopt.

## What RACS Does

RACS (Risk-Aware Coordination System) is an open-source framework that enables autonomous systems to **anticipate operational risk, coordinate responses, and prevent cascading failures.**

### Architecture

RACS uses a two-layer architecture:

**Layer 1 — Site Agents** (per-facility)  
Each facility runs a local Site Agent that:
- Connects to robots and automation systems via vendor-agnostic interfaces
- Generates real-time risk signals (congestion probability, failure likelihood, recovery latency) using ML models (XGBoost/LightGBM)
- Executes local coordination decisions (task redistribution, controlled slowdowns)
- Enforces hard safety constraints with human override capability

**Layer 2 — Network Brain** (cross-facility)  
A coordination layer subscribes to risk signals from all Site Agents and:
- Computes cross-site capacity and workload balance
- Detects potential cascading failures before they propagate
- Issues coordination commands to redistribute work across facilities
- Maintains complete audit trail of all decisions

### Key Principles

1. **Risk-Aware by Default**: Every coordination decision incorporates predictive risk signals — not just current state
2. **Safety as a Hard Constraint**: Safety limits (speed, zones, e-stop) cannot be overridden by optimization. Human override is always available.
3. **Vendor-Agnostic**: Protocol specifications use open standards (JSON Schema, Apache Kafka, ROS 2 compatible) — no vendor lock-in
4. **Graceful Degradation**: When faults occur, the system degrades to a known safe state rather than failing unpredictably
5. **Auditable**: Every decision is logged with full context for post-incident analysis and regulatory compliance
6. **Decentralized Resilience**: No single point of failure — if the Network Brain fails, each Site Agent continues operating autonomously in safe mode

## Quick Start

```bash
git clone https://github.com/ridwanbale/racs-protocols
cd racs-protocols
pip install -e ".[dev]"
python examples/quickstart.py
```

### Minimal example

```python
from racs.risk.predictor import RiskPredictor
from racs.risk.risk_signals import TelemetryInput
from racs.safety.constraints import SafetyConfig, SafetyGate

# Describe your facility's current state
telemetry = TelemetryInput(
    site_id="WAREHOUSE_1",
    queue_length=45,
    robot_active_count=12,
    robot_fault_count=2,
    throughput_rate=0.73,
    error_rate_5min=0.08,
)

# Predict risk
predictor = RiskPredictor()
signal = predictor.predict(telemetry)
print(f"Risk level: {signal.level.value} (score={signal.composite_score:.2f})")
# Risk level: HIGH (score=0.71)

# Evaluate a coordination action
gate = SafetyGate(SafetyConfig.default())
result = gate.evaluate({
    "type": "redistribute_tasks",
    "target_site_robot_density": 0.70,
    "robot_speed_ms": 1.5,
    "target_fault_ratio": 0.05,
    "risk_score": signal.composite_score,
})
print(f"Action allowed: {result.allowed} — {result.reason}")
```

## Simulation Scenarios

RACS includes pre-built simulation scenarios demonstrating coordination under stress:

| Scenario | What It Tests | Key Metric |
|----------|--------------|------------|
| `lane_backup` | Congestion at one facility triggers cross-site load rebalancing | Cascade containment time |
| `robot_failure` | Single robot fault with coordinated recovery | Recovery time, safety violations |
| `demand_spike` | Sudden demand surge requiring multi-site response | Throughput maintenance % |
| `network_partition` | Communication failure between sites | Autonomous safe-state transition |
| `crowdstrike_scenario` | Centralized update failure affecting multiple sites | Decentralized resilience score |

Run all scenarios:
```bash
make simulate
```

Run a specific scenario:
```bash
python simulations/scenarios/crowdstrike_scenario.py
```

## Benchmarks

```bash
make benchmark
```

Baseline results (from `benchmarks/baseline_results.json`):

| Scenario | Mode | Recovery Time | Cascade Radius | Prevention Rate |
|----------|------|--------------|---------------|----------------|
| Robot failure | Without RACS | 28 steps | 2 sites | — |
| Robot failure | With RACS | **14 steps** | **0 sites** | — |
| Cascade prevention | Without RACS | — | 3 sites | 0% |
| Cascade prevention | With RACS | 18 steps | **1 site** | **67%** |
| CrowdStrike update failure | Without RACS | — | — | 0% |
| CrowdStrike update failure | With RACS | — | — | **100%** |

## NIST AI Risk Management Framework Alignment

RACS is designed to align with all four functions of the [NIST AI RMF 1.0](https://www.nist.gov/itl/ai-risk-management-framework):

| NIST Function | RACS Implementation |
|---------------|-------------------|
| **GOVERN** | Safety constraints are policy-level YAML configurations, not code-level decisions |
| **MAP** | Risk signals are explicitly categorized by type, severity, and confidence |
| **MEASURE** | Standardized benchmarks measure recovery time, containment, and prevention rate |
| **MANAGE** | Human override, controlled degradation, and audit logging |

See [NIST_ALIGNMENT.md](NIST_ALIGNMENT.md) for detailed mapping.

## Repository Structure

```
racs-protocols/
├── racs/                        # Core library
│   ├── agents/                  # Site Agent and Network Brain
│   ├── risk/                    # Risk prediction and cascade detection
│   ├── coordination/            # Task allocation, load balancing, recovery
│   ├── safety/                  # Constraints, human override, degradation, audit
│   └── protocols/               # Kafka events, ROS 2 mappings, JSON Schemas
├── simulations/                 # Multi-warehouse simulation + scenarios
├── benchmarks/                  # Standardized benchmark suite
├── examples/                    # Quickstart and demos
├── tests/                       # Test suite (pytest)
└── docs/                        # Protocol spec, safety playbook, deployment guide
```

## Running Tests

```bash
make test
```

## Related Work

- Bale, R.I. et al. (2026). "Integration of Digital Twin Platforms with Machine Learning Models for Early Detection of Project Delays and Cost Overruns." *Asian Journal of Advanced Research and Reports*, 20(1), 332–349.
- NIST AI 100-1: AI Risk Management Framework 1.0 (January 2023)
- DARPA Resilient Supply-and-Demand Networks (RSDN) Program
- ARM Institute Technology Project Call 24-01: Multi-Robot and Multi-Human Collaboration

## Contributing

Contributions are welcome. Please open an issue to discuss your proposed change before submitting a pull request.

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.

## Author

**Ridwan Ishola Bale**  
MS Engineering, Purdue University | MS Engineering with Finance, University of Kent  
Independent researcher in risk-aware AI coordination for autonomous logistics systems
