# Deployment Guide

## Overview

RACS is designed as a reference implementation. Deploying it in a real facility requires integration with your existing robot fleet management system. This guide describes the integration points.

## Prerequisites

- Python 3.10+
- Robot fleet that exposes telemetry (queue lengths, robot counts, error rates)
- Optional: Apache Kafka for multi-site transport (in-memory bus used otherwise)
- Optional: ROS 2 Humble or later for robot interface

## Installation

```bash
git clone https://github.com/ridwanbale/racs-protocols
cd racs-protocols
pip install -e ".[dev]"
make test  # verify installation
```

## Single-Site Deployment

```python
from racs.agents.site_agent import SiteAgent, AgentConfig
from racs.risk.risk_signals import TelemetryInput

agent = SiteAgent(AgentConfig(site_id="MY_WAREHOUSE"))

# In your control loop:
while True:
    telemetry = TelemetryInput(
        site_id="MY_WAREHOUSE",
        queue_length=your_system.get_queue_length(),
        robot_active_count=your_system.get_active_robots(),
        robot_fault_count=your_system.get_faulted_robots(),
        throughput_rate=your_system.get_throughput_fraction(),
        error_rate_5min=your_system.get_error_rate(),
    )
    signal = agent.tick(telemetry)
    # Apply degradation: agent.speed_factor controls robot speed
    your_system.set_speed_limit(agent.degradation_level.speed_factor * MAX_SPEED)
    time.sleep(1)
```

## Multi-Site Deployment

1. Deploy one `SiteAgent` per facility
2. Deploy one `NetworkBrain` (can run on any node with network access to all sites)
3. Connect agents to the brain via the `on_signal_publish` callback
4. Use Apache Kafka for production transport (replace in-memory callbacks with Kafka producers/consumers)

## Customising Safety Policy

Copy and modify `safety_policy.yaml`:

```yaml
max_robot_speed_ms: 1.5        # your facility's speed limit
min_robot_spacing_m: 2.0       # your aisle width constraint
max_site_robot_density: 0.75   # conservative for your fleet size
max_fault_ratio: 0.15
emergency_stop_risk_threshold: 0.95
escalate_risk_threshold: 0.70
exclusion_zones:
  - CHARGING_STATION
  - MAINTENANCE_BAY
```

Load it:
```python
from racs.safety.constraints import SafetyConfig, SafetyGate
gate = SafetyGate(SafetyConfig.from_file("safety_policy.yaml"))
```

## Kafka Integration

```python
from racs.protocols.kafka_events import KafkaEventPublisher

publisher = KafkaEventPublisher(bootstrap_servers="kafka-broker:9092")
publisher.publish_risk_signal(signal)
```

## ROS 2 Integration

Map RACS types to ROS 2 messages using `ROS2MessageMapper`:

```python
from racs.protocols.ros2_interfaces import ROS2MessageMapper
ros_msg = ROS2MessageMapper.risk_signal_to_ros2(signal)
# Publish ros_msg via your ROS 2 node
```
