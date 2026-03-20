# Notebook & Databricks Support Design

**Date**: 2026-03-20
**Status**: Approved
**Branch**: `feat/notebook-support`
**Dependency**: R language support (PR #43) must be merged first for R cell parsing

## Summary

Extend the existing Jupyter notebook parser to support:

1. **Regular notebooks with non-Python kernels** (R) — currently skipped, route through R parser from PR #43
2. **Databricks multi-language `.ipynb` notebooks** — cells with `%python`, `%sql`, `%r` magic prefixes routed to per-language parsers
3. **Databricks `.py` notebook exports** — files starting with `# Databricks notebook source`, split on `# COMMAND ----------` delimiters, `# MAGIC` prefixes determine cell language
4. **SQL table extraction** — regex-based extraction of table references from SQL cells as `IMPORTS_FROM` edges

## Current State

Basic `.ipynb` support is committed on `feat/notebook-support`:
- Parses Python-kernel notebooks only (rejects non-Python kernels)
- Extracts functions, classes, imports, calls from code cells
- Filters `%pip`, `!ls` magic/shell lines
- Tracks `cell_index` in `node.extra`
- 12 tests passing

## Design

### 1. Multi-language cell routing in `.ipynb`

**Change**: Remove the Python-only kernel gate. Instead, group cells by language and parse each group with its respective parser.

**Language detection per cell**:
- If a code cell's first line starts with `%python`, `%sql`, `%r` → that's the cell language (strip the magic line)
- If no magic prefix → use the kernel's default language from `metadata.kernelspec.language`
- `%scala`, `%md`, `%sh` → skip entire cell
- `%pip`, `!` lines → filter individual lines (existing behavior)

**Supported languages**: Python, R, SQL. Cells in unsupported languages (e.g. Scala) are silently skipped.

**Per-language parsing**:
- **Python cells**: Concatenate, parse with Python Tree-sitter (existing behavior)
- **R cells**: Concatenate, parse with R Tree-sitter (requires R node type mappings from PR #43)
- **SQL cells**: Regex scan for table references (see section 4)

**`cell_index` tracking**: All nodes are tagged with the absolute cell position in the notebook (0-based index from `enumerate(cells)`), regardless of language. This is consistent with the existing behavior.

### 2. Databricks `.py` notebook detection and parsing

**Detection**: In `parse_bytes` (around line 262), add a new branch: if `detect_language` returns `"python"` and the file content starts with `# Databricks notebook source\n`, route to `_parse_databricks_py_notebook` instead of the normal Python parser. This check goes in `parse_bytes` itself, not in `detect_language` (which is used by other callers).

**Cell splitting**: Split the file content on lines matching `# COMMAND ----------` (with optional trailing whitespace). Each segment between delimiters is a cell.

**Language detection per cell**:
- If all non-empty lines start with `# MAGIC %sql` → SQL cell (strip `# MAGIC ` prefix from each line)
- If all non-empty lines start with `# MAGIC %r` → R cell (strip `# MAGIC ` prefix)
- If all non-empty lines start with `# MAGIC %md` or `# MAGIC %sh` → skip cell
- If no lines have `# MAGIC` prefix → Python cell
- Mixed cells (some `# MAGIC`, some not) → treat as Python (Databricks enforces one language per cell, so this is a degenerate case)

**Parsing**: Same per-language routing as `.ipynb` via the shared `_parse_notebook_cells` method.

### 3. Shared cell abstraction

Introduce a lightweight named tuple:

```python
class CellInfo(NamedTuple):
    index: int
    language: str
    source: str
```

Both `.ipynb` and `.py` notebook paths produce `list[CellInfo]`, which feeds into a shared `_parse_notebook_cells` method that:

1. Groups cells by language
2. Concatenates each group with offset tracking (maintaining absolute `cell_index`)
3. Parses Python/R groups through Tree-sitter
4. Scans SQL groups for table references
5. Tags all nodes with `cell_index` (absolute notebook position)
6. Resolves call targets and generates `TESTED_BY` edges

### 4. SQL table extraction

Regex pattern (case-insensitive):
```python
_SQL_TABLE_RE = re.compile(
    r"(?:FROM|JOIN|INTO|CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW)|INSERT\s+OVERWRITE)"
    r"\s+(`?\w+`?(?:\.`?\w+`?)*)",
    re.IGNORECASE,
)
```

Each match produces an `IMPORTS_FROM` edge from the notebook file to the table name. Backtick-quoted identifiers are stripped of backticks. CTEs, subqueries, and functions are not parsed — this is intentionally simple and covers the dominant patterns in Databricks notebooks.

### 5. File node metadata

- `.ipynb` files: `language` field on the `File` node uses the kernel's default language
- Databricks `.py` files: `language` field is `"python"` (the export format), with `extra["notebook_format"] = "databricks_py"` to distinguish from regular Python files

## Test Plan

### New fixtures
- `tests/fixtures/sample_databricks_notebook.ipynb` — mixed Python/SQL/R cells with magic prefixes
- `tests/fixtures/sample_databricks_export.py` — Databricks `.py` export with `COMMAND` delimiters and `MAGIC` prefixes

### New test classes
- `TestDatabricksNotebookParsing` — multi-language `.ipynb`
  - Detects cell languages from magic prefixes
  - Parses Python functions from `%python` cells
  - Parses R functions from `%r` cells
  - Extracts SQL table references as `IMPORTS_FROM` edges
  - Skips `%scala`, `%md`, `%sh` cells
  - Falls back to kernel language for cells without magic prefix

- `TestDatabricksPyNotebook` — `.py` export format
  - Detects Databricks header (`# Databricks notebook source`)
  - Splits cells on `# COMMAND ----------`
  - Parses Python code from non-MAGIC cells
  - Extracts SQL tables from `# MAGIC %sql` cells
  - Parses R code from `# MAGIC %r` cells
  - Skips `# MAGIC %md` and `# MAGIC %sh` cells
  - Regular `.py` files (no header) parse normally

- `TestRKernelNotebook` — R-kernel `.ipynb`
  - R-kernel notebook parses R code in cells
  - Detects R functions, assignments, classes

### Negative/error test cases
- Malformed Databricks `.py` file (header present but no `COMMAND` delimiters) — treat as single Python cell
- Empty cells (all lines are magic/shell) — skip gracefully
- `.py` file where `# Databricks notebook source` appears on line 2 — treat as regular Python
- SQL cells with no valid table references (e.g., `SELECT 1+1`) — no edges, no errors
- Notebook with conflicting kernel metadata — prefer `kernelspec.language` over `language_info.name`

### Updated tests
- `test_non_python_kernel` → update to verify R kernel notebooks are now parsed (not skipped)

## Architecture Decisions

1. **No Tree-sitter for SQL** — regex is sufficient for table reference extraction; adding a SQL grammar would be overkill for this use case
2. **Header-based detection for `.py` exports** — `# Databricks notebook source` on line 1 is definitive; heuristic-based detection would risk false positives on regular Python files
3. **Shared `CellInfo` abstraction** — avoids duplicating parsing logic between `.ipynb` and `.py` notebook paths
4. **Python + R + SQL only** — Scala would require a new Tree-sitter grammar, out of scope
5. **Detection in `parse_bytes`, not `detect_language`** — keeps Databricks detection isolated from other callers of `detect_language`
6. **Mixed cells treated as Python** — Databricks enforces one language per cell; mixed is degenerate, not worth complexity
