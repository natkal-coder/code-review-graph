"""In-memory context graph with LRU-K eviction and thread-safe access tracking.

Maintains a hot cache of accessed nodes from the main graph, enabling fast O(1)
lookups and intelligent eviction based on recency + frequency scoring.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any, Optional

from .agent_detect import AgentInfo
from .context_config import ContextConfig
from .context_node import ContextNode, compute_relevance

logger = logging.getLogger(__name__)


class ContextGraph:
    """In-memory hot cache of frequently-accessed graph nodes with LRU-K eviction."""

    def __init__(self, config: ContextConfig, agent: AgentInfo) -> None:
        """Initialize context graph with capacity based on agent's context window.

        Args:
            config: Context configuration (max_tokens, eviction_threshold, etc.)
            agent: Detected AI agent info (context_window, overhead)
        """
        self.config = config
        self.agent = agent
        self._store: dict[str, ContextNode] = {}
        self._lock = threading.Lock()
        self._current_token_usage = 0
        self._created_at = time.time()

        logger.info(
            "ContextGraph initialized for %s (capacity: %d tokens)",
            agent.name,
            agent.effective_capacity(),
        )

    def record_access(
        self,
        qualified_name: str,
        kind: str,
        token_estimate: int,
        tool_name: str,
        query_context: Optional[str] = None,
    ) -> None:
        """Record access to a node. Upserts into store, triggers eviction if needed.

        Args:
            qualified_name: Qualified name of accessed node
            kind: Node kind (File, Class, Function, etc.)
            token_estimate: Estimated tokens for this node
            tool_name: Name of tool that accessed this node
            query_context: Optional context about the query
        """
        with self._lock:
            if qualified_name not in self._store:
                # New node: add to store
                ctx_node = ContextNode(
                    qualified_name=qualified_name,
                    kind=kind,
                    token_estimate=token_estimate,
                )
                self._store[qualified_name] = ctx_node
                self._current_token_usage += token_estimate
            else:
                # Existing node: just record access
                ctx_node = self._store[qualified_name]

            # Record the access
            ctx_node.record_access(tool_name, query_context)

            # Evict if needed
            if self.capacity_ratio() > self.config.eviction_threshold:
                self._evict_lru_k()

    def get_context(self, qualified_name: str) -> Optional[ContextNode]:
        """Get a context node by qualified name (O(1) lookup).

        Args:
            qualified_name: Qualified name of the node

        Returns:
            ContextNode if found, None otherwise
        """
        with self._lock:
            return self._store.get(qualified_name)

    def current_token_usage(self) -> int:
        """Get current token usage of context graph."""
        with self._lock:
            return self._current_token_usage

    def capacity_ratio(self) -> float:
        """Get current usage as ratio of effective capacity (0-1).

        Returns:
            Ratio of current_usage / effective_capacity
        """
        with self._lock:
            effective_capacity = self.agent.effective_capacity()
            if effective_capacity <= 0:
                return 1.0
            return self._current_token_usage / effective_capacity

    def active_context(self) -> list[ContextNode]:
        """Get all context nodes sorted by relevance (highest first).

        Returns:
            List of ContextNode sorted by relevance_score descending
        """
        with self._lock:
            now = time.time()
            nodes = list(self._store.values())
            # Score each node
            scored = [
                (compute_relevance(node, now), node) for node in nodes
            ]
            # Sort descending
            scored.sort(key=lambda x: x[0], reverse=True)
            return [node for _, node in scored]

    def summary(self) -> dict[str, Any]:
        """Get summary stats of context graph.

        Returns:
            Dict with stats: nodes_count, total_tokens, capacity_ratio, agent_type, etc.
        """
        with self._lock:
            now = time.time()
            active = self.active_context()
            return {
                "nodes_count": len(self._store),
                "total_tokens": self._current_token_usage,
                "effective_capacity": self.agent.effective_capacity(),
                "capacity_ratio": self.capacity_ratio(),
                "agent_type": self.agent.name,
                "agent_context_window": self.agent.context_window,
                "eviction_threshold": self.config.eviction_threshold,
                "lifetime_seconds": now - self._created_at,
                "active_nodes": [
                    {
                        "qualified_name": node.qualified_name,
                        "kind": node.kind,
                        "access_count": node.access_count,
                        "last_accessed": node.last_accessed,
                        "frequency_score": round(node.frequency_score, 3),
                        "token_estimate": node.token_estimate,
                    }
                    for node in active[:10]  # Top 10
                ],
            }

    def clear(self) -> None:
        """Clear all nodes from context graph."""
        with self._lock:
            self._store.clear()
            self._current_token_usage = 0
            logger.info("ContextGraph cleared")

    def _evict_lru_k(self) -> None:
        """Evict least-valuable nodes via LRU-K scoring until below threshold.

        This is an internal method called under lock. Uses LRU-K scoring:
        score = frequency_score × exp(-time_since_access / τ)
        where τ=60 seconds is the time constant for recency decay.
        """
        now = time.time()
        target_usage = int(
            self.agent.effective_capacity() * (self.config.eviction_threshold - 0.15)
        )

        evicted_count = 0
        evicted_tokens = 0

        while self._current_token_usage > target_usage and self._store:
            # Score all nodes
            scores = {}
            for name, node in self._store.items():
                score = self._score_node(node, now)
                scores[name] = score

            # Find lowest score
            min_name = min(scores.keys(), key=lambda k: scores[k])
            min_node = self._store.pop(min_name)
            evicted_tokens += min_node.token_estimate
            self._current_token_usage -= min_node.token_estimate
            evicted_count += 1

        if evicted_count > 0:
            logger.debug(
                "ContextGraph evicted %d nodes (%d tokens)",
                evicted_count,
                evicted_tokens,
            )

    def _score_node(self, node: ContextNode, now: float) -> float:
        """Score a node for eviction: lower = evict first.

        Uses LRU-K: recent accesses + high frequency = high score.

        Args:
            node: Node to score
            now: Current timestamp

        Returns:
            Score in [0, 1], higher = keep, lower = evict
        """
        time_since = now - node.last_accessed

        # Recency decay: exp(-t / τ) where τ = 60 seconds
        recency = math.exp(-time_since / 60.0)

        # Frequency: already in [0, 1] from EMA
        frequency = node.frequency_score

        # Combined: 0.5 recency + 0.5 frequency
        return 0.5 * recency + 0.5 * frequency
