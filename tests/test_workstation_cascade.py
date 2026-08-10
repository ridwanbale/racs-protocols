"""Tests for downstream workstation starvation cascade mechanics."""

from dataclasses import replace

from simulations.warehouse_sim import SimulationScenarioConfig, WarehouseSimulation


def config(**overrides) -> SimulationScenarioConfig:
    base = dict(
        steps=6,
        step_duration_s=10.0,
        site_ids=("SITE_A",),
        robot_count=1,
        seed=123,
        fault_site="SITE_A",
        fault_step=0,
        fault_count=0,
        initial_site_queue={"SITE_A": 0},
        initial_site_demand={"SITE_A": 1.0},
        tasks_per_step=0,
        base_service_steps=1,
        task_deadline_steps=10,
        workstation_enabled=True,
        workstation_processing_rate=1.0,
        initial_workstation_buffer={"SITE_A": 0},
    )
    base.update(overrides)
    return SimulationScenarioConfig(**base)


def site_steps(metrics):
    return [step["sites"]["SITE_A"] for step in metrics]


def test_amr_completion_deposits_one_workstation_unit() -> None:
    sim = WarehouseSimulation(
        config=config(steps=2, initial_site_queue={"SITE_A": 1}, workstation_processing_rate=0),
        with_racs=False,
    )

    metrics = sim.run()

    assert site_steps(metrics)[1]["tasks_completed_step"] == 1
    assert site_steps(metrics)[1]["workstation_input_buffer"] == 1
    assert sim._sites["SITE_A"].workstation_input_buffer == ["SITE_A_T000000"]


def test_workstation_processes_integer_rate_deterministically() -> None:
    metrics = WarehouseSimulation(
        config=config(
            steps=1,
            workstation_processing_rate=2,
            initial_workstation_buffer={"SITE_A": 3},
        ),
        with_racs=False,
    ).run()

    site = site_steps(metrics)[0]
    assert site["workstation_completed_this_step"] == 2
    assert site["downstream_tasks_completed"] == 2
    assert site["workstation_input_buffer"] == 1


def test_fractional_workstation_rate_uses_deterministic_credit() -> None:
    metrics = WarehouseSimulation(
        config=config(
            steps=4,
            workstation_processing_rate=0.5,
            initial_workstation_buffer={"SITE_A": 3},
        ),
        with_racs=False,
    ).run()

    assert [site["workstation_completed_this_step"] for site in site_steps(metrics)] == [
        0,
        1,
        0,
        1,
    ]


def test_workstation_cannot_process_more_units_than_buffer_contains() -> None:
    metrics = WarehouseSimulation(
        config=config(
            steps=1,
            workstation_processing_rate=3,
            initial_workstation_buffer={"SITE_A": 1},
        ),
        with_racs=False,
    ).run()

    site = site_steps(metrics)[0]
    assert site["workstation_completed_this_step"] == 1
    assert site["workstation_starved_this_step"] is False


def test_missed_processing_units_capture_partial_shortfall() -> None:
    metrics = WarehouseSimulation(
        config=config(
            steps=1,
            workstation_processing_rate=2,
            initial_workstation_buffer={"SITE_A": 1},
            initial_site_queue={"SITE_A": 2},
            base_service_steps=10,
        ),
        with_racs=False,
    ).run()

    site = site_steps(metrics)[0]
    assert site["workstation_processing_entitlement"] == 2
    assert site["workstation_completed_this_step"] == 1
    assert site["missed_workstation_processing_units_this_step"] == 1
    assert site["missed_workstation_processing_units"] == 1


def test_zero_processing_entitlement_never_misses_processing_units() -> None:
    metrics = WarehouseSimulation(
        config=config(
            steps=1,
            workstation_processing_rate=0.5,
            initial_workstation_buffer={"SITE_A": 0},
        ),
        with_racs=False,
    ).run()

    site = site_steps(metrics)[0]
    assert site["workstation_processing_entitlement"] == 0
    assert site["missed_workstation_processing_units_this_step"] == 0


def test_starvation_boundary_requires_prior_or_initial_workstation_work() -> None:
    startup = WarehouseSimulation(
        config=config(steps=1, workstation_processing_rate=1),
        with_racs=False,
    ).run()
    starved = WarehouseSimulation(
        config=config(
            steps=1,
            workstation_processing_rate=2,
            initial_workstation_buffer={"SITE_A": 1},
            initial_site_queue={"SITE_A": 2},
            base_service_steps=10,
        ),
        with_racs=False,
    ).run()

    assert site_steps(startup)[0]["workstation_starved_this_step"] is False
    assert site_steps(starved)[0]["workstation_starved_this_step"] is True


def test_startup_handling_avoids_artificial_cascade_before_delivery() -> None:
    metrics = WarehouseSimulation(
        config=config(
            steps=3,
            initial_site_queue={"SITE_A": 1},
            base_service_steps=3,
            workstation_processing_rate=1,
            degradation_enabled=True,
            degradation_site="SITE_A",
            degradation_robot_id="SITE_A_R000",
            degradation_start_step=0,
            degradation_rate_per_step=0,
            minimum_service_capacity=1,
            hard_failure_capacity_threshold=0,
        ),
        with_racs=False,
    ).run()

    assert [site["workstation_starved_this_step"] for site in site_steps(metrics)[:2]] == [
        False,
        False,
    ]
    assert all(
        site["post_degradation_starvation_started"] is False
        for site in site_steps(metrics)[:2]
    )


def test_healthy_stable_scenario_does_not_persistently_starve() -> None:
    metrics = WarehouseSimulation(
        config=config(
            steps=20,
            robot_count=4,
            tasks_per_step=0.85,
            base_service_steps=4,
            workstation_processing_rate=0.85,
        ),
        with_racs=True,
    ).run()

    warm = site_steps(metrics)[8:]
    assert sum(site["workstation_starved_this_step"] for site in warm) == 0
    assert all(step["intervention_step"] is None for step in metrics)


def test_delayed_amr_delivery_can_cause_workstation_starvation() -> None:
    metrics = WarehouseSimulation(
        config=config(
            steps=8,
            initial_workstation_buffer={"SITE_A": 1},
            initial_site_queue={"SITE_A": 2},
            base_service_steps=5,
            workstation_processing_rate=1,
        ),
        with_racs=False,
    ).run()

    assert any(site["workstation_starved_this_step"] for site in site_steps(metrics))


def test_recovered_delivery_restores_downstream_processing() -> None:
    metrics = WarehouseSimulation(
        config=config(
            steps=8,
            robot_count=2,
            initial_workstation_buffer={"SITE_A": 1},
            initial_site_queue={"SITE_A": 2},
            base_service_steps=2,
            workstation_processing_rate=1,
            degradation_enabled=True,
            degradation_site="SITE_A",
            degradation_robot_id="SITE_A_R000",
            degradation_start_step=1,
            degradation_rate_per_step=1.0,
            minimum_service_capacity=0.2,
            hard_failure_capacity_threshold=0.2,
        ),
        with_racs=False,
    ).run()

    steps = site_steps(metrics)
    assert any(site["workstation_starved_this_step"] for site in steps)
    assert any(site["workstation_completed_this_step"] > 0 for site in steps[3:])


def test_post_degradation_starvation_start_step_is_first_raw_qualifying_starvation() -> None:
    metrics = WarehouseSimulation(
        config=config(
            steps=6,
            initial_workstation_buffer={"SITE_A": 1},
            initial_site_queue={"SITE_A": 2},
            base_service_steps=5,
            workstation_processing_rate=1,
            degradation_enabled=True,
            degradation_site="SITE_A",
            degradation_robot_id="SITE_A_R000",
            degradation_start_step=2,
            degradation_rate_per_step=0,
            minimum_service_capacity=1,
            hard_failure_capacity_threshold=0,
        ),
        with_racs=False,
    ).run()

    starvation_steps = [
        index
        for index, site in enumerate(site_steps(metrics))
        if site["workstation_starved_this_step"] and index >= 2
    ]
    assert site_steps(metrics)[-1]["post_degradation_starvation_start_step"] == starvation_steps[0]
    assert site_steps(metrics)[-1]["post_degradation_starvation_steps"] == len(starvation_steps)


def test_post_degradation_starvation_steps_counts_separated_events() -> None:
    metrics = WarehouseSimulation(
        config=config(
            steps=60,
            robot_count=4,
            tasks_per_step=0.85,
            base_service_steps=4,
            workstation_processing_rate=0.85,
            degradation_enabled=True,
            degradation_site="SITE_A",
            degradation_robot_id="SITE_A_R000",
            degradation_start_step=15,
            degradation_rate_per_step=0.1,
            minimum_service_capacity=0.2,
            hard_failure_capacity_threshold=0.2,
        ),
        with_racs=False,
    ).run()

    starvation_steps = [
        index
        for index, site in enumerate(site_steps(metrics))
        if site["workstation_starved_this_step"] and index >= 15
    ]

    assert starvation_steps == [19, 38, 50, 58]
    assert site_steps(metrics)[-1]["post_degradation_starvation_steps"] == 4
    assert site_steps(metrics)[-1]["post_degradation_starvation_steps"] != (
        starvation_steps[-1] - starvation_steps[0] + 1
    )


def test_affected_resource_count_changes_only_with_downstream_impact() -> None:
    no_cascade = WarehouseSimulation(
        config=config(steps=2, workstation_processing_rate=0),
        with_racs=False,
    ).run()
    cascade = WarehouseSimulation(
        config=config(
            steps=2,
            initial_workstation_buffer={"SITE_A": 1},
            initial_site_queue={"SITE_A": 2},
            base_service_steps=10,
            workstation_processing_rate=2,
            degradation_enabled=True,
            degradation_site="SITE_A",
            degradation_robot_id="SITE_A_R000",
            degradation_start_step=0,
            degradation_rate_per_step=0,
            minimum_service_capacity=1,
            hard_failure_capacity_threshold=0,
        ),
        with_racs=False,
    ).run()

    assert site_steps(no_cascade)[-1]["affected_resource_count"] == 1
    assert site_steps(cascade)[-1]["affected_resource_count"] == 2


def test_baseline_and_racs_share_workstation_mechanics_before_intervention() -> None:
    scenario = config(
        steps=12,
        robot_count=4,
        tasks_per_step=0.85,
        base_service_steps=4,
        workstation_processing_rate=0.85,
        degradation_enabled=True,
        degradation_site="SITE_A",
        degradation_robot_id="SITE_A_R000",
        degradation_start_step=6,
        degradation_rate_per_step=0.1,
        minimum_service_capacity=0.2,
        hard_failure_capacity_threshold=0.2,
    )

    baseline = WarehouseSimulation(config=scenario, with_racs=False).run()
    racs = WarehouseSimulation(config=scenario, with_racs=True).run()
    intervention = next(
        (step["intervention_step"] for step in racs if step["intervention_step"] is not None),
        len(racs),
    )

    for step in range(intervention):
        assert site_steps(baseline)[step]["workstation_input_buffer"] == site_steps(racs)[step][
            "workstation_input_buffer"
        ]
        assert site_steps(baseline)[step]["downstream_tasks_completed"] == site_steps(racs)[step][
            "downstream_tasks_completed"
        ]
        assert baseline[step]["service_capacity_by_step"] == racs[step]["service_capacity_by_step"]


def test_workstation_disabled_preserves_existing_transport_metrics() -> None:
    disabled = config(workstation_enabled=False, initial_site_queue={"SITE_A": 1})
    enabled_zero_rate = replace(disabled, workstation_enabled=True, workstation_processing_rate=0)

    disabled_metrics = WarehouseSimulation(config=disabled, with_racs=False).run()
    enabled_metrics = WarehouseSimulation(config=enabled_zero_rate, with_racs=False).run()

    for disabled_step, enabled_step in zip(site_steps(disabled_metrics), site_steps(enabled_metrics)):
        assert disabled_step["tasks_created"] == enabled_step["tasks_created"]
        assert disabled_step["tasks_completed"] == enabled_step["tasks_completed"]
        assert disabled_step["queue"] == enabled_step["queue"]


def precondition_config(**overrides) -> SimulationScenarioConfig:
    base = dict(
        steps=20,
        step_duration_s=10.0,
        site_ids=("SITE_A",),
        robot_count=4,
        seed=1000,
        fault_site="SITE_A",
        fault_step=0,
        fault_count=0,
        initial_site_queue={"SITE_A": 0},
        initial_site_demand={"SITE_A": 1.0},
        tasks_per_step=0.85,
        base_service_steps=4,
        task_deadline_steps=16,
        workstation_enabled=True,
        workstation_processing_rate=0.85,
        initial_workstation_buffer={"SITE_A": 0},
        degradation_enabled=True,
        degradation_site="SITE_A",
        degradation_robot_id="SITE_A_R000",
        degradation_start_step=15,
        degradation_rate_per_step=0.1,
        minimum_service_capacity=0.2,
        hard_failure_capacity_threshold=0.2,
    )
    base.update(overrides)
    return SimulationScenarioConfig(**base)


def test_no_degradation_start_preconditioning_config_preserves_existing_behavior() -> None:
    without_config = precondition_config()
    with_empty_config = replace(without_config, workstation_buffer_at_degradation_start={})

    first = WarehouseSimulation(config=without_config, with_racs=False).run()
    second = WarehouseSimulation(config=with_empty_config, with_racs=False).run()

    assert [site["workstation_input_buffer"] for site in site_steps(first)] == [
        site["workstation_input_buffer"] for site in site_steps(second)
    ]
    assert all(site["workstation_preconditioned_this_step"] is False for site in site_steps(first))


def test_degradation_start_preconditioning_establishes_exact_wip_values() -> None:
    for target in (1, 2, 4):
        metrics = WarehouseSimulation(
            config=precondition_config(
                workstation_buffer_at_degradation_start={"SITE_A": target}
            ),
            with_racs=False,
        ).run()
        site = site_steps(metrics)[15]

        assert site["workstation_preconditioned_this_step"] is True
        assert site["workstation_buffer_after_preconditioning"] == target
        assert site["preconditioned_workstation_units"] == target
        assert site["workstation_preconditioned_step"] == 15


def test_degradation_start_preconditioning_can_explicitly_establish_zero_wip() -> None:
    metrics = WarehouseSimulation(
        config=precondition_config(
            steps=16,
            tasks_per_step=0,
            workstation_processing_rate=0,
            initial_workstation_buffer={"SITE_A": 4},
            workstation_buffer_at_degradation_start={"SITE_A": 0},
        ),
        with_racs=False,
    ).run()
    site = site_steps(metrics)[15]

    assert site["workstation_preconditioned_this_step"] is True
    assert site["workstation_buffer_before_preconditioning"] == 4
    assert site["workstation_buffer_after_preconditioning"] == 0
    assert site["workstation_input_buffer"] == 0
    assert site["preconditioned_workstation_units"] == 0


def test_degradation_start_preconditioning_occurs_exactly_once_and_is_not_replenished() -> None:
    metrics = WarehouseSimulation(
        config=precondition_config(
            steps=18,
            tasks_per_step=0,
            workstation_processing_rate=1,
            workstation_buffer_at_degradation_start={"SITE_A": 1},
        ),
        with_racs=False,
    ).run()
    sites = site_steps(metrics)

    assert [i for i, site in enumerate(sites) if site["workstation_preconditioned_this_step"]] == [15]
    assert sites[15]["workstation_buffer_before_processing"] == 1
    assert sites[15]["workstation_input_buffer"] == 0
    assert sites[16]["workstation_preconditioned_this_step"] is False
    assert sites[16]["workstation_input_buffer"] == 0


def test_degradation_start_preconditioning_is_rejected_without_workstation() -> None:
    try:
        precondition_config(
            workstation_enabled=False,
            workstation_buffer_at_degradation_start={"SITE_A": 1},
        )
    except ValueError as exc:
        assert "workstation_buffer_at_degradation_start" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_degradation_start_preconditioning_validates_sites_and_values() -> None:
    for kwargs in (
        {"workstation_buffer_at_degradation_start": {"SITE_B": 1}},
        {"workstation_buffer_at_degradation_start": {"SITE_A": -1}},
    ):
        try:
            precondition_config(**kwargs)
        except ValueError as exc:
            assert "workstation_buffer_at_degradation_start" in str(exc)
        else:
            raise AssertionError("expected ValueError")


def test_baseline_and_racs_receive_identical_preconditioned_wip_and_credit() -> None:
    scenario = precondition_config(
        workstation_buffer_at_degradation_start={"SITE_A": 4},
    )

    baseline = WarehouseSimulation(config=scenario, with_racs=False).run()
    racs = WarehouseSimulation(config=scenario, with_racs=True).run()

    for key in (
        "workstation_buffer_after_preconditioning",
        "workstation_buffer_before_processing",
        "workstation_processing_entitlement",
        "preconditioned_workstation_units",
    ):
        assert site_steps(baseline)[15][key] == site_steps(racs)[15][key]
    assert baseline[15]["service_capacity_by_step"] == racs[15]["service_capacity_by_step"]


def test_preconditioned_units_are_distinguished_from_transport_deliveries() -> None:
    metrics = WarehouseSimulation(
        config=precondition_config(
            steps=18,
            tasks_per_step=0,
            workstation_processing_rate=1,
            workstation_buffer_at_degradation_start={"SITE_A": 2},
        ),
        with_racs=False,
    ).run()
    final = site_steps(metrics)[-1]

    assert final["preconditioned_workstation_units"] == 2
    assert final["transport_delivered_to_workstation"] == 0
    assert final["downstream_preconditioned_completed"] == 2
    assert final["downstream_transport_completed"] == 0
    assert final["downstream_tasks_completed"] == 2


def test_cascade_metrics_ignore_pre_degradation_startup_starvation() -> None:
    metrics = WarehouseSimulation(
        config=precondition_config(
            initial_workstation_buffer={"SITE_A": 1},
            workstation_buffer_at_degradation_start={"SITE_A": 4},
        ),
        with_racs=False,
    ).run()

    pre_degradation_starvation = [
        index
        for index, site in enumerate(site_steps(metrics)[:15])
        if site["workstation_starved_this_step"]
    ]

    assert pre_degradation_starvation
    assert site_steps(metrics)[14]["post_degradation_starvation_started"] is False
    assert site_steps(metrics)[-1]["post_degradation_starvation_start_step"] is None
