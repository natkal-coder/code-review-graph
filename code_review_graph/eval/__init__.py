"""Evaluation framework for code-review-graph.

Provides scoring metrics (token efficiency, MRR, precision/recall),
benchmark runners, and report generators for benchmarking graph-based code reviews.
"""

from __future__ import annotations

from .reporter import generate_full_report, generate_markdown_report, generate_readme_tables
from .runner import load_all_configs, load_config, run_eval, write_csv
from .scorer import compute_mrr, compute_precision_recall, compute_token_efficiency

__all__ = [
    "compute_mrr",
    "compute_precision_recall",
    "compute_token_efficiency",
    "generate_full_report",
    "generate_markdown_report",
    "generate_readme_tables",
    "load_all_configs",
    "load_config",
    "run_eval",
    "write_csv",
]
