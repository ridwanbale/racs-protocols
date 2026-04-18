"""Multi-agent consensus protocol for distributed coordination decisions."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class Vote(Enum):
    AGREE = "AGREE"
    DISAGREE = "DISAGREE"
    ABSTAIN = "ABSTAIN"


@dataclass
class ConsensusProposal:
    proposal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    proposed_by: str = ""
    action: dict = field(default_factory=dict)
    votes: Dict[str, Vote] = field(default_factory=dict)
    quorum: int = 2
    created_at: float = field(default_factory=time.time)
    decided_at: Optional[float] = None
    accepted: Optional[bool] = None

    def cast_vote(self, agent_id: str, vote: Vote) -> None:
        self.votes[agent_id] = vote
        self._check_quorum()

    def _check_quorum(self) -> None:
        agree = sum(1 for v in self.votes.values() if v == Vote.AGREE)
        disagree = sum(1 for v in self.votes.values() if v == Vote.DISAGREE)
        participating = len([v for v in self.votes.values() if v != Vote.ABSTAIN])

        if agree >= self.quorum:
            self.accepted = True
            self.decided_at = time.time()
        elif disagree >= self.quorum or (participating >= self.quorum and agree == 0):
            self.accepted = False
            self.decided_at = time.time()

    @property
    def is_decided(self) -> bool:
        return self.accepted is not None

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "proposed_by": self.proposed_by,
            "action": self.action,
            "votes": {agent: vote.value for agent, vote in self.votes.items()},
            "quorum": self.quorum,
            "accepted": self.accepted,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
        }


class ConsensusProtocol:
    """
    Lightweight multi-agent consensus for cross-site coordination decisions.

    Agents vote on proposed actions; a quorum of AGREE votes is required before execution.
    This prevents any single agent from unilaterally affecting peer sites.
    """

    def __init__(self, quorum: int = 2) -> None:
        self._quorum = quorum
        self._proposals: Dict[str, ConsensusProposal] = {}

    def propose(self, proposer_id: str, action: dict) -> ConsensusProposal:
        proposal = ConsensusProposal(
            proposed_by=proposer_id,
            action=action,
            quorum=self._quorum,
        )
        self._proposals[proposal.proposal_id] = proposal
        proposal.cast_vote(proposer_id, Vote.AGREE)
        return proposal

    def vote(self, proposal_id: str, agent_id: str, vote: Vote) -> ConsensusProposal:
        proposal = self._proposals[proposal_id]
        if proposal.is_decided:
            raise ValueError(f"Proposal {proposal_id} is already decided")
        proposal.cast_vote(agent_id, vote)
        return proposal

    def get(self, proposal_id: str) -> Optional[ConsensusProposal]:
        return self._proposals.get(proposal_id)

    def pending(self) -> List[ConsensusProposal]:
        return [p for p in self._proposals.values() if not p.is_decided]

    def expire_old(self, max_age_s: float = 120.0) -> List[ConsensusProposal]:
        now = time.time()
        expired = []
        for pid, proposal in list(self._proposals.items()):
            if not proposal.is_decided and now - proposal.created_at > max_age_s:
                proposal.accepted = False
                proposal.decided_at = now
                expired.append(proposal)
        return expired
