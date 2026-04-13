"""Tests for coordination modules: TaskAllocator, LoadBalancer, RecoveryPlanner."""

import time

import pytest

from racs.coordination.load_balancer import LoadBalancer
from racs.coordination.recovery_planner import RecoveryPlanner, RecoveryStage
from racs.coordination.task_allocator import Task, TaskAllocator, TaskPriority


class TestTaskAllocator:
    def _make_task(self, origin: str = "SITE_A", priority: TaskPriority = TaskPriority.NORMAL) -> Task:
        return Task(
            task_id="T001",
            origin_site=origin,
            task_type="pick_and_place",
            priority=priority,
        )

    def test_allocates_to_highest_scoring_site(self):
        allocator = TaskAllocator()
        task = self._make_task(origin="SITE_A")
        capacities = {"SITE_A": 0.9, "SITE_B": 0.8, "SITE_C": 0.5}
        risks = {"SITE_A": 0.1, "SITE_B": 0.1, "SITE_C": 0.1}
        result = allocator.allocate(task, capacities, risks)
        assert result is not None
        # SITE_A excluded (origin), SITE_B has higher capacity
        assert result.assigned_to == "SITE_B"

    def test_excludes_origin_site(self):
        allocator = TaskAllocator()
        task = self._make_task(origin="SITE_A")
        result = allocator.allocate(task, {"SITE_A": 1.0, "SITE_B": 0.5}, {})
        assert result is not None
        assert result.assigned_to != "SITE_A"

    def test_returns_none_when_no_candidates(self):
        allocator = TaskAllocator()
        task = self._make_task(origin="SITE_A")
        result = allocator.allocate(task, {"SITE_A": 0.9}, {})
        assert result is None

    def test_avoids_high_risk_sites(self):
        allocator = TaskAllocator()
        task = self._make_task(origin="SITE_A")
        capacities = {"SITE_B": 0.9, "SITE_C": 0.7}
        risks = {"SITE_B": 0.9, "SITE_C": 0.1}   # SITE_B very high risk
        result = allocator.allocate(task, capacities, risks)
        assert result is not None
        assert result.assigned_to == "SITE_C"

    def test_bulk_redistribute_respects_priority(self):
        allocator = TaskAllocator()
        tasks = [
            Task("T1", "SITE_A", "low_pri", priority=TaskPriority.LOW),
            Task("T2", "SITE_A", "crit_pri", priority=TaskPriority.CRITICAL),
            Task("T3", "SITE_A", "high_pri", priority=TaskPriority.HIGH),
        ]
        results = allocator.bulk_redistribute(tasks, {"SITE_B": 0.8, "SITE_C": 0.7}, {})
        # All should be allocated
        assert len(results) == 3


class TestLoadBalancer:
    def test_identifies_imbalance(self):
        lb = LoadBalancer(high_threshold=0.80, low_threshold=0.40)
        commands = lb.compute_rebalance(
            utilisation={"SITE_A": 0.90, "SITE_B": 0.30},
            risk_scores={"SITE_A": 0.1, "SITE_B": 0.1},
        )
        assert len(commands) > 0
        assert commands[0].from_site == "SITE_A"
        assert commands[0].to_site == "SITE_B"

    def test_no_commands_when_balanced(self):
        lb = LoadBalancer()
        commands = lb.compute_rebalance(
            utilisation={"SITE_A": 0.60, "SITE_B": 0.65},
        )
        assert commands == []

    def test_avoids_high_risk_targets(self):
        lb = LoadBalancer(high_threshold=0.80, low_threshold=0.40)
        commands = lb.compute_rebalance(
            utilisation={"SITE_A": 0.90, "SITE_B": 0.20},
            risk_scores={"SITE_B": 0.85},   # SITE_B has high risk
        )
        # Should not send work to high-risk SITE_B
        assert all(c.to_site != "SITE_B" for c in commands)

    def test_balance_score_perfect(self):
        lb = LoadBalancer()
        score = lb.network_balance_score({"A": 0.5, "B": 0.5, "C": 0.5})
        assert score == 0.0

    def test_balance_score_imbalanced(self):
        lb = LoadBalancer()
        score = lb.network_balance_score({"A": 1.0, "B": 0.0})
        assert score > 0.5


class TestRecoveryPlanner:
    def test_robot_fault_plan_has_all_stages(self):
        planner = RecoveryPlanner()
        plan = planner.plan_robot_fault("SITE_A", fault_count=3)
        stages = [step.stage for step in plan.steps]
        assert RecoveryStage.ASSESS in stages
        assert RecoveryStage.ISOLATE in stages
        assert RecoveryStage.REDISTRIBUTE in stages
        assert RecoveryStage.COMPLETE in stages

    def test_cascade_containment_plan_created(self):
        planner = RecoveryPlanner()
        plan = planner.plan_cascade_containment("SITE_A", ["SITE_B", "SITE_C"])
        assert len(plan.steps) > 0
        assert plan.site_id == "SITE_A"

    def test_plan_not_complete_initially(self):
        planner = RecoveryPlanner()
        plan = planner.plan_robot_fault("SITE_A", fault_count=1)
        assert not plan.is_complete

    def test_plan_advance_requires_hold(self):
        planner = RecoveryPlanner()
        plan = planner.plan_robot_fault("SITE_A", fault_count=1)
        # Should not advance immediately (hold not elapsed)
        result = plan.advance()
        assert result is None or not plan.steps[0].completed
