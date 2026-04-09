"""Context node tracking for in-memory hot cache with access history.

Tracks file access patterns during a session with frequency scoring for LRU-K eviction.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Optional

from .parser import NodeInfo


@dataclass(frozen=True)
class AccessLog:
    """Single access record with timestamp and tool context."""

    timestamp: float
    tool_name: str
    query_context: Optional[str] = None


@dataclass
class ContextNode:
    """In-memory representation of a frequently-accessed graph node."""

    qualified_name: str
    kind: str  # File, Class, Function, Type, Test
    token_estimate: int
    access_log: list[AccessLog] = field(default_factory=list)
    access_count: int = 0
    frequency_score: float = 0.0
    last_accessed: float = field(default_factory=time.time)
    first_accessed: float = field(default_factory=time.time)

    def record_access(
        self, tool_name: str, query_context: Optional[str] = None
    ) -> None:
        """Record a new access. Keeps only last 2 for LRU-K."""
        now = time.time()
        self.access_log.append(AccessLog(now, tool_name, query_context))

        # LRU-K: keep only last 2 accesses (recency + 1 prior for frequency)
        if len(self.access_log) > 2:
            self.access_log = self.access_log[-2:]

        self.access_count += 1
        self.last_accessed = now

        # Update frequency_score: EMA of inter-access time
        if len(self.access_log) >= 2:
            time_delta = self.access_log[-1].timestamp - self.access_log[-2].timestamp
            # Shorter time between accesses = higher frequency
            freq = 1.0 / (1.0 + max(time_delta, 0.001))
        else:
            freq = 1.0
        # EMA: 0.7 * old + 0.3 * new
        self.frequency_score = 0.7 * self.frequency_score + 0.3 * freq

    def time_since_access(self) -> float:
        """Age of this node in seconds since last access."""
        return time.time() - self.last_accessed


def estimate_tokens(node: NodeInfo) -> int:
    """Heuristic token estimation: (line_end - line_start) * 15.

    Assumes ~15 tokens per line on average (conservative estimate).
    """
    if node.line_end is not None and node.line_start is not None:
        lines = max(1, node.line_end - node.line_start)
        return lines * 15
    return 50  # Default for nodes without line info


def compute_relevance(ctx_node: ContextNode, now: float) -> float:
    """Compute relevance score: weighted combo of recency, frequency, connectivity.

    Args:
        ctx_node: The context node to score
        now: Current timestamp

    Returns:
        Relevance score in [0, 1], higher = more relevant to keep
    """
    time_since = now - ctx_node.last_accessed

    # Recency decay: exp(-t / τ) where τ = 60 seconds
    recency = math.exp(-time_since / 60.0)

    # Frequency score already in [0, 1]
    frequency = ctx_node.frequency_score

    # Access count contrib (log-scaled to avoid dominance)
    access_contrib = math.log(ctx_node.access_count + 1) / math.log(101)  # cap at 100

    # Weighted combination: 0.5 * recency + 0.3 * frequency + 0.2 * access_count
    return 0.5 * recency + 0.3 * frequency + 0.2 * access_contrib
