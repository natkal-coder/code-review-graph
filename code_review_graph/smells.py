"""Code smell detection from graph query results.

Analyzes GraphNode instances against the knowledge graph to detect
anti-patterns and assign smell tags with severity and confidence scores.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from code_review_graph.graph import GraphStore


# ---------------------------------------------------------------------------
# Smell result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Smell:
    tag: str
    severity: str  # critical, high, medium, low
    confidence: float  # 0.0 - 1.0
    detail: str = ""

    def to_dict(self) -> dict:
        d: dict = {"tag": self.tag, "severity": self.severity, "confidence": self.confidence}
        if self.detail:
            d["detail"] = self.detail
        return d


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

GOD_OBJECT_METHOD_THRESHOLD = 20
GOD_OBJECT_LINE_THRESHOLD = 500
LONG_PARAM_THRESHOLD = 5
DEEP_NESTING_THRESHOLD = 4

# Magic number regex: bare numeric literals (int/float) not inside comments or
# common harmless values (0, 1, -1, 2).  Runs per-line on source.
_MAGIC_NUMBER_RE = re.compile(
    r"(?<![a-zA-Z_\"\'])"  # not preceded by identifier/string char
    r"-?(?!(?:0|1|2|-1)(?:\b))\d+\.?\d*"  # number that is not 0/1/2/-1
    r"(?![a-zA-Z_\"\'])"  # not followed by identifier/string char
)
_COMMENT_RE = re.compile(r"^\s*(?:#|//|/\*|\*)")
_CONSTANT_ASSIGN_RE = re.compile(r"^[A-Z_][A-Z0-9_]*\s*=")

# Silent catch: bare except/catch with empty or pass-only body
_SILENT_CATCH_PY_RE = re.compile(
    r"except\b[^:]*:\s*\n\s*(?:pass\s*$|\s*$)", re.MULTILINE
)
_SILENT_CATCH_JS_RE = re.compile(
    r"catch\s*\([^)]*\)\s*\{\s*\}", re.MULTILINE
)


# ---------------------------------------------------------------------------
# Individual detectors
# ---------------------------------------------------------------------------


def detect_god_object(node: Any, graph: "GraphStore") -> Smell | None:
    """Detect classes with too many methods or too many lines."""
    if node.kind != "Class":
        return None

    # Count CONTAINS edges pointing to methods/functions
    edges = graph.get_edges_by_source(node.qualified_name)
    method_count = sum(
        1 for e in edges if e.kind == "CONTAINS"
    )

    line_span = (node.line_end - node.line_start + 1) if node.line_end > 0 else 0

    is_god = False
    parts: list[str] = []

    if method_count > GOD_OBJECT_METHOD_THRESHOLD:
        is_god = True
        parts.append(f"{method_count} methods (>{GOD_OBJECT_METHOD_THRESHOLD})")
    if line_span > GOD_OBJECT_LINE_THRESHOLD:
        is_god = True
        parts.append(f"{line_span} lines (>{GOD_OBJECT_LINE_THRESHOLD})")

    if not is_god:
        return None

    # Higher confidence when both triggers fire
    confidence = 0.95 if len(parts) == 2 else 0.8
    return Smell(
        tag="god_object",
        severity="high",
        confidence=confidence,
        detail="; ".join(parts),
    )


def detect_long_param_list(node: Any) -> Smell | None:
    """Detect functions/methods with too many parameters."""
    if node.kind not in ("Function", "Method"):
        return None

    param_count = node.extra.get("param_count", 0)
    if not param_count:
        # Fallback: count from params string
        if node.params:
            param_count = len([p for p in node.params.split(",") if p.strip()])
        else:
            return None

    if param_count <= LONG_PARAM_THRESHOLD:
        return None

    return Smell(
        tag="long_param_list",
        severity="medium",
        confidence=1.0,
        detail=f"{param_count} params (>{LONG_PARAM_THRESHOLD})",
    )


def detect_deep_nesting(node: Any) -> Smell | None:
    """Detect deeply nested functions/methods."""
    if node.kind not in ("Function", "Method"):
        return None

    depth = node.extra.get("nesting_depth", 0)
    if depth <= DEEP_NESTING_THRESHOLD:
        return None

    severity = "high" if depth > 6 else "medium"
    return Smell(
        tag="deep_nesting",
        severity=severity,
        confidence=0.9,
        detail=f"depth {depth} (>{DEEP_NESTING_THRESHOLD})",
    )


def detect_magic_numbers(node: Any, source: str) -> Smell | None:
    """Detect bare numeric literals in source that are not constants or in comments."""
    if node.kind not in ("Function", "Method", "Class"):
        return None

    # Extract relevant source lines
    start = max(node.line_start - 1, 0)
    end = node.line_end if node.line_end > 0 else start + 1
    lines = source.splitlines()[start:end]

    magic_count = 0
    for line in lines:
        stripped = line.strip()
        if _COMMENT_RE.match(stripped):
            continue
        if _CONSTANT_ASSIGN_RE.match(stripped):
            continue
        magic_count += len(_MAGIC_NUMBER_RE.findall(line))

    if magic_count == 0:
        return None

    severity = "medium" if magic_count >= 5 else "low"
    confidence = min(0.6 + magic_count * 0.05, 0.9)
    return Smell(
        tag="magic_numbers",
        severity=severity,
        confidence=round(confidence, 2),
        detail=f"{magic_count} magic number(s) found",
    )


def detect_silent_catch(node: Any, source: str) -> Smell | None:
    """Detect catch/except blocks with no logging, re-raise, or handling."""
    if node.kind not in ("Function", "Method"):
        return None

    start = max(node.line_start - 1, 0)
    end = node.line_end if node.line_end > 0 else start + 1
    snippet = "\n".join(source.splitlines()[start:end])

    found = bool(_SILENT_CATCH_PY_RE.search(snippet) or _SILENT_CATCH_JS_RE.search(snippet))
    if not found:
        return None

    return Smell(
        tag="silent_catch",
        severity="high",
        confidence=0.85,
        detail="catch/except block with no handling",
    )


def detect_unused_imports(node: Any, graph: "GraphStore") -> Smell | None:
    """Detect import nodes where the imported symbol is never called from the same file."""
    if node.kind != "File":
        return None

    # Get all IMPORTS_FROM edges from this file
    file_nodes = graph.get_nodes_by_file(node.file_path)

    import_targets: list[str] = []
    for fn in file_nodes:
        for edge in graph.get_edges_by_source(fn.qualified_name):
            if edge.kind == "IMPORTS_FROM":
                import_targets.append(edge.target_qualified)

    if not import_targets:
        return None

    # Collect all CALLS targets from nodes in this file
    call_targets: set[str] = set()
    for fn in file_nodes:
        for edge in graph.get_edges_by_source(fn.qualified_name):
            if edge.kind == "CALLS":
                call_targets.add(edge.target_qualified)

    unused = [t for t in import_targets if t not in call_targets]
    if not unused:
        return None

    return Smell(
        tag="unused_imports",
        severity="low",
        confidence=0.7,
        detail=f"{len(unused)} potentially unused: {', '.join(unused[:5])}",
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

# Detectors grouped by what they need
_DETECTORS_NODE_ONLY: list = [detect_long_param_list, detect_deep_nesting]
_DETECTORS_WITH_GRAPH: list = [detect_god_object, detect_unused_imports]
_DETECTORS_WITH_SOURCE: list = [detect_magic_numbers, detect_silent_catch]


def analyze_node(
    node: Any,
    graph: "GraphStore | None" = None,
    source: str = "",
) -> list[dict]:
    """Run all smell detectors on a node and return smell dicts.

    Args:
        node: The graph node to analyze.
        graph: The graph store for querying edges/relations.
        source: Full file source text (needed for magic_numbers, silent_catch).

    Returns:
        List of smell dicts with tag, severity, confidence, and optional detail.
    """
    smells: list[dict] = []

    for detector in _DETECTORS_NODE_ONLY:
        result = detector(node)
        if result is not None:
            smells.append(result.to_dict())

    if graph is not None:
        for detector in _DETECTORS_WITH_GRAPH:
            result = detector(node, graph)
            if result is not None:
                smells.append(result.to_dict())

    if source:
        for detector in _DETECTORS_WITH_SOURCE:
            result = detector(node, source)
            if result is not None:
                smells.append(result.to_dict())

    return smells


def analyze_file(
    file_path: str,
    graph: "GraphStore",
    source: str = "",
) -> dict[str, list[dict]]:
    """Run smell detection on all nodes in a file.

    Returns:
        Dict mapping qualified_name -> list of smell dicts.
    """
    nodes = graph.get_nodes_by_file(file_path)
    results: dict[str, list[dict]] = {}
    for node in nodes:
        smells = analyze_node(node, graph, source)
        if smells:
            results[node.qualified_name] = smells
    return results
