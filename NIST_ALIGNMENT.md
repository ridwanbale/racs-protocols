# NIST AI Risk Management Framework Alignment

RACS is designed to align with all four functions of the [NIST AI RMF 1.0](https://www.nist.gov/itl/ai-risk-management-framework) (January 2023).

| NIST Function | NIST Category | RACS Component | Implementation Detail |
|:---|:---|:---|:---|
| **GOVERN** | GV-1: Policies & Procedures | `racs/safety/constraints.py` | Safety policies are YAML configuration files, not hardcoded values. Policy changes go through version control, separating governance from implementation. |
| **GOVERN** | GV-4: Organisational Teams | `racs/safety/human_override.py` | Every high-risk action requires explicit human operator approval. Operators are identified by `operator_id` in every override decision. |
| **GOVERN** | GV-6: Policies include feedback | `racs/safety/audit_log.py` | Complete audit trail of every safety and coordination decision, including operator IDs, timestamps, and justifications. Supports post-incident review. |
| **MAP** | MP-2: Context is established | `racs/risk/risk_signals.py` | Risk signals are explicitly typed (congestion, failure, recovery) with confidence scores. Context (site_id, timestamp, source telemetry) is always included. |
| **MAP** | MP-3: Benefits and costs quantified | `benchmarks/metrics.py` | Standardised metrics quantify performance (throughput, recovery time) against safety (violation count). Enables explicit cost/benefit analysis of coordination decisions. |
| **MAP** | MP-5: Likelihood and impact assessed | `racs/risk/cascade_detector.py` | Cascade probability is computed across the network. Impact radius (number of sites affected) is estimated before a cascade occurs. |
| **MEASURE** | MS-1: Metrics identified | `benchmarks/metrics.py` | Five primary metrics: recovery time, cascade containment radius, prevention rate, safety violation count, human override latency. |
| **MEASURE** | MS-2: AI system behaviours tracked | `racs/risk/risk_aggregator.py` | Continuous monitoring of all site risk levels. Stale signals are detected and flagged. Network-level composite score is always available. |
| **MEASURE** | MS-4: Feedback incorporated | `benchmarks/runner.py` | Benchmark suite runs all scenarios and saves baseline results. Re-running after changes quantifies improvement or regression. |
| **MANAGE** | MG-1: Identified risks prioritised | `racs/agents/network_brain.py` | NetworkBrain prioritises responses by composite risk score. Sites at CRITICAL level receive immediate cascade response before rebalancing. |
| **MANAGE** | MG-2: Risk treatments applied | `racs/safety/controlled_degradation.py` | Controlled degradation applies graduated response: reduced speed → limited operations → safe hold → emergency stop. Never fails unpredictably. |
| **MANAGE** | MG-3: Responses to risks managed | `racs/safety/human_override.py` | Escalation workflow with configurable timeout. Timed-out requests default to NOT executing — the safe choice. |
| **MANAGE** | MG-4: Residual risks tracked | `racs/safety/audit_log.py` | SAFETY_BLOCKED and SAFETY_ESCALATED events form a residual risk register. Reviewable at any time without additional tooling. |

## Key Design Choices Supporting NIST Alignment

**Separation of governance from optimisation:** Safety constraints live in YAML; optimisation logic lives in Python. A policy change never requires code deployment.

**Auditability by default:** The `AuditLog` is always enabled — there is no mode in which RACS makes unlogged decisions.

**Human primacy for high-risk actions:** The `HumanOverrideSystem` defaults to rejection on timeout. Automation serves as a recommendation engine for high-risk decisions, not the final authority.

**Measurable outcomes:** Every scenario in `simulations/scenarios/` produces quantified metrics. Claims about RACS effectiveness are verifiable by re-running the benchmark suite.
