"""Agent discovery and registration."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class AgentStatus(Enum):
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"


@dataclass
class AgentInfo:
    agent_id: str
    site_id: str
    host: str = "localhost"
    port: int = 8080
    status: AgentStatus = AgentStatus.ONLINE
    registered_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)
    capabilities: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "site_id": self.site_id,
            "host": self.host,
            "port": self.port,
            "status": self.status.value,
            "registered_at": self.registered_at,
            "last_heartbeat": self.last_heartbeat,
            "age_s": time.time() - self.last_heartbeat,
        }


class AgentRegistry:
    """Central registry for discovering and tracking active site agents."""

    def __init__(self, heartbeat_timeout_s: float = 30.0) -> None:
        self._agents: Dict[str, AgentInfo] = {}
        self._timeout_s = heartbeat_timeout_s

    def register(self, info: AgentInfo) -> None:
        self._agents[info.agent_id] = info

    def deregister(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)

    def heartbeat(self, agent_id: str, status: AgentStatus = AgentStatus.ONLINE) -> None:
        if agent_id in self._agents:
            self._agents[agent_id].last_heartbeat = time.time()
            self._agents[agent_id].status = status

    def get(self, agent_id: str) -> Optional[AgentInfo]:
        return self._agents.get(agent_id)

    def by_site(self, site_id: str) -> Optional[AgentInfo]:
        return next((a for a in self._agents.values() if a.site_id == site_id), None)

    def online_agents(self) -> List[AgentInfo]:
        cutoff = time.time() - self._timeout_s
        return [
            a for a in self._agents.values()
            if a.last_heartbeat >= cutoff and a.status != AgentStatus.OFFLINE
        ]

    def stale_agents(self) -> List[AgentInfo]:
        cutoff = time.time() - self._timeout_s
        return [a for a in self._agents.values() if a.last_heartbeat < cutoff]

    def mark_stale_offline(self) -> List[AgentInfo]:
        stale = self.stale_agents()
        for a in stale:
            a.status = AgentStatus.OFFLINE
        return stale

    def all_site_ids(self) -> List[str]:
        return [a.site_id for a in self.online_agents()]
