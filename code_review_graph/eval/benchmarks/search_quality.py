"""Search quality benchmark: measures search result ranking via MRR."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def run(repo_path: Path, store, config: dict) -> list[dict]:
    """Run search quality benchmark."""
    results = []
    for sq in config.get("search_queries", []):
        query = sq["query"]
        expected = sq["expected"]

        try:
            from code_review_graph.search import hybrid_search
            search_results = hybrid_search(store, query, limit=20)
        except Exception:
            # Fallback to basic search
            search_results = [
                {"qualified_name": n.qualified_name}
                for n in store.search_nodes(query, limit=20)
            ]

        rank = 0
        for i, r in enumerate(search_results):
            if isinstance(r, dict):
                qn = r.get("qualified_name", "")
            elif hasattr(r, "qualified_name"):
                qn = r.qualified_name
            else:
                qn = ""
            if expected in qn or qn in expected:
                rank = i + 1
                break

        results.append({
            "repo": config["name"],
            "query": query,
            "expected": expected,
            "rank": rank,
            "reciprocal_rank": round(1.0 / rank if rank > 0 else 0.0, 3),
        })
    return results
