# Design Decisions

## Why Two-Layer Architecture (Site Agent + Network Brain)?

A single centralised coordinator is a single point of failure — exactly the failure mode demonstrated by the July 2024 CrowdStrike incident. The two-layer design ensures that if the Network Brain goes offline, each Site Agent continues operating in a safe degraded mode. The Network Brain enhances coordination but is never required for safety.

## Why Heuristic Fallback in the Risk Predictor?

XGBoost requires training data and the xgboost library. In environments where neither is available, the heuristic fallback (`_HeuristicFallback`) provides usable risk estimates from first principles. This matches the ARM Institute's finding that deployable systems must work under real operational constraints, not just in ideal research conditions.

## Why YAML for Safety Configuration?

Safety policies (speed limits, density thresholds) are separated from code so that:
- Policy changes don't require code deployments
- Qualified operators can adjust limits without engineering involvement
- Git history provides an audit trail of policy changes independent of code changes

## Why Greedy Best-Fit for Task Allocation?

Optimal task allocation is NP-hard at scale. The greedy best-fit heuristic (highest capacity × lowest risk) provides good solutions in O(n log n) time with no external solver dependency. OR-Tools is available in the NetworkBrain for cases where optimality matters.

## Why Require Quorum for Cross-Site Commands?

The ConsensusProtocol prevents any single agent from unilaterally affecting peer sites. A minimum quorum of 2 votes (proposer + one peer) ensures that coordination decisions have network-level agreement before taking effect.

## Why Staged Recovery?

Immediate return to full operation after a fault risks re-triggering the same failure. The RecoveryPlanner enforces minimum hold periods at each recovery stage, allowing conditions to stabilise before the next step. This mirrors standard industrial safety practices (IEC 62061, ISO 13849).
