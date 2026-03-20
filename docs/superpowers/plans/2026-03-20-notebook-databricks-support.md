# Notebook & Databricks Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the notebook parser to support multi-language cells (Python/R/SQL), Databricks `.ipynb` magic prefixes, Databricks `.py` notebook exports, and SQL table reference extraction.

**Architecture:** Refactor `_parse_notebook` into a shared `_parse_notebook_cells` method that accepts `list[CellInfo]`. Both `.ipynb` and Databricks `.py` exports produce `CellInfo` lists, which get grouped by language and routed to the appropriate parser (Tree-sitter for Python/R, regex for SQL).

**Tech Stack:** Python 3.10+, Tree-sitter (via `tree_sitter_language_pack`), regex for SQL, pytest

**Spec:** `docs/superpowers/specs/2026-03-20-notebook-databricks-support-design.md`

**Branch:** `feat/notebook-support` (already exists with basic `.ipynb` Python support)

**Dependency:** R language support (PR #43) must be merged for R cell parsing to work. R-related tests should be written but may be skipped if R parser mappings aren't available yet.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `code_review_graph/parser.py` | Modify | Add `CellInfo`, `_SQL_TABLE_RE`, `_parse_notebook_cells`, `_parse_databricks_py_notebook`; refactor `_parse_notebook` |
| `tests/test_notebook.py` | Modify | Add `TestDatabricksNotebookParsing`, `TestDatabricksPyNotebook`, `TestRKernelNotebook`, negative tests; update `test_non_python_kernel` |
| `tests/fixtures/sample_databricks_notebook.ipynb` | Create | Mixed Python/SQL/R cells with magic prefixes |
| `tests/fixtures/sample_databricks_export.py` | Create | Databricks `.py` export with COMMAND delimiters and MAGIC prefixes |

---

## Task 1: Add CellInfo and SQL regex to parser.py

**Files:**
- Modify: `code_review_graph/parser.py:1-20` (imports/constants area)

- [ ] **Step 1: Add CellInfo NamedTuple and SQL regex**

Add after the existing imports (line 15), before `logger`:

```python
from typing import NamedTuple, Optional

class CellInfo(NamedTuple):
    index: int
    language: str
    source: str

_SQL_TABLE_RE = re.compile(
    r"(?:FROM|JOIN|INTO|CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW)|INSERT\s+OVERWRITE)"
    r"\s+((?:`[^`]+`|\w+)(?:\.(?:`[^`]+`|\w+))*)",
    re.IGNORECASE,
)
```

Note: `Optional` is already imported from `typing` on line 15. Change that import to also include `NamedTuple`.

- [ ] **Step 2: Verify no syntax errors**

Run: `python -c "from code_review_graph.parser import CellInfo, _SQL_TABLE_RE; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add code_review_graph/parser.py
git commit -m "feat(parser): add CellInfo and SQL table regex"
```

---

## Task 2: SQL table extraction tests and implementation

**Files:**
- Modify: `tests/test_notebook.py`
- Modify: `code_review_graph/parser.py`

- [ ] **Step 1: Write SQL table extraction tests**

Add to `tests/test_notebook.py`:

```python
from code_review_graph.parser import _SQL_TABLE_RE


class TestSqlTableExtraction:
    def test_from_clause(self):
        matches = _SQL_TABLE_RE.findall("SELECT * FROM my_table")
        assert "my_table" in matches

    def test_qualified_table(self):
        matches = _SQL_TABLE_RE.findall("SELECT * FROM catalog.schema.table")
        assert "catalog.schema.table" in matches

    def test_join(self):
        matches = _SQL_TABLE_RE.findall(
            "SELECT * FROM a JOIN b ON a.id = b.id"
        )
        assert "a" in matches
        assert "b" in matches

    def test_insert_into(self):
        matches = _SQL_TABLE_RE.findall("INSERT INTO target_table VALUES (1)")
        assert "target_table" in matches

    def test_create_table(self):
        matches = _SQL_TABLE_RE.findall("CREATE TABLE my_db.new_table (id INT)")
        assert "my_db.new_table" in matches

    def test_create_or_replace_view(self):
        matches = _SQL_TABLE_RE.findall(
            "CREATE OR REPLACE VIEW my_view AS SELECT 1"
        )
        assert "my_view" in matches

    def test_insert_overwrite(self):
        matches = _SQL_TABLE_RE.findall(
            "INSERT OVERWRITE catalog.schema.tbl SELECT * FROM src"
        )
        assert "catalog.schema.tbl" in matches
        assert "src" in matches

    def test_backtick_quoted(self):
        matches = _SQL_TABLE_RE.findall("SELECT * FROM `my-catalog`.`schema`.`table`")
        # findall returns the outer capture group; backticks preserved
        assert any("my-catalog" in m for m in matches)

    def test_no_table_refs(self):
        matches = _SQL_TABLE_RE.findall("SELECT 1 + 1")
        assert matches == []

    def test_case_insensitive(self):
        matches = _SQL_TABLE_RE.findall("select * from My_Table")
        assert "My_Table" in matches
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/test_notebook.py::TestSqlTableExtraction -v`
Expected: All PASS (these test the regex directly, no implementation needed beyond Task 1)

- [ ] **Step 3: Commit**

```bash
git add tests/test_notebook.py
git commit -m "test: add SQL table regex extraction tests"
```

---

## Task 3: Refactor _parse_notebook into shared _parse_notebook_cells

This is the core refactor. The existing `_parse_notebook` method gets split: cell extraction stays in `_parse_notebook`, and the parsing logic moves to `_parse_notebook_cells` which accepts `list[CellInfo]`.

**Files:**
- Modify: `code_review_graph/parser.py:430-554`

- [ ] **Step 1: Add _parse_notebook_cells method**

Add this new method to the `CodeParser` class, after the existing `_parse_notebook` method (after line 554):

```python
def _parse_notebook_cells(
    self,
    path: Path,
    cells: list[CellInfo],
    default_language: str,
) -> tuple[list[NodeInfo], list[EdgeInfo]]:
    """Parse notebook cells grouped by language.

    Args:
        path: Notebook file path.
        cells: List of CellInfo with index, language, and source.
        default_language: Default language for the File node.
    """
    file_path_str = str(path)
    test_file = _is_test_file(file_path_str)

    # Group cells by language
    lang_cells: dict[str, list[CellInfo]] = {}
    for cell in cells:
        lang_cells.setdefault(cell.language, []).append(cell)

    all_nodes: list[NodeInfo] = []
    all_edges: list[EdgeInfo] = []

    # Track offsets per language for cell_index tagging
    # Each entry: (cell_index, concat_start_line, concat_end_line)
    # Kept per-language since each language group is parsed independently
    # by Tree-sitter (line numbers restart at 1 for each group).
    all_cell_offsets: list[tuple[int, int, int]] = []
    max_line = 1

    for lang, lang_group in lang_cells.items():
        if lang == "sql":
            # SQL: regex-based table extraction
            for cell in lang_group:
                for match in _SQL_TABLE_RE.finditer(cell.source):
                    table_name = match.group(1).replace("`", "")
                    all_edges.append(EdgeInfo(
                        kind="IMPORTS_FROM",
                        source=file_path_str,
                        target=table_name,
                        file_path=file_path_str,
                        line=1,
                    ))
            continue

        if lang not in ("python", "r"):
            continue

        ts_parser = self._get_parser(lang)
        if not ts_parser:
            continue

        # Concatenate cells of this language
        # Line numbers start at 1 for each language group because
        # Tree-sitter parses each concatenation independently.
        code_chunks: list[str] = []
        cell_offsets: list[tuple[int, int, int]] = []
        current_line = 1  # always starts at 1 per language group

        for cell in lang_group:
            cell_line_count = cell.source.count("\n") + (
                1 if not cell.source.endswith("\n") else 0
            )
            cell_offsets.append((
                cell.index, current_line, current_line + cell_line_count - 1,
            ))
            code_chunks.append(cell.source)
            current_line += cell_line_count + 1

        concatenated = "\n".join(code_chunks)
        concat_bytes = concatenated.encode("utf-8")

        tree = ts_parser.parse(concat_bytes)

        import_map, defined_names = self._collect_file_scope(
            tree.root_node, lang, concat_bytes,
        )
        self._extract_from_tree(
            tree.root_node, concat_bytes, lang,
            file_path_str, all_nodes, all_edges,
            import_map=import_map, defined_names=defined_names,
        )

        all_cell_offsets.extend(cell_offsets)
        max_line = max(max_line, current_line)

    # Create File node
    file_node = NodeInfo(
        kind="File",
        name=file_path_str,
        file_path=file_path_str,
        line_start=1,
        line_end=max_line,
        language=default_language,
        is_test=test_file,
    )
    all_nodes.insert(0, file_node)

    # Resolve call targets
    all_edges = self._resolve_call_targets(
        all_nodes, all_edges, file_path_str,
    )

    # Tag nodes with cell_index
    for node in all_nodes:
        if node.kind == "File":
            continue
        for cell_idx, start, end in all_cell_offsets:
            if start <= node.line_start <= end:
                node.extra["cell_index"] = cell_idx
                break

    # Generate TESTED_BY edges
    if test_file:
        test_qnames = set()
        for n in all_nodes:
            if n.is_test:
                qn = self._qualify(n.name, n.file_path, n.parent_name)
                test_qnames.add(qn)
        for edge in list(all_edges):
            if edge.kind == "CALLS" and edge.source in test_qnames:
                all_edges.append(EdgeInfo(
                    kind="TESTED_BY",
                    source=edge.target,
                    target=edge.source,
                    file_path=edge.file_path,
                    line=edge.line,
                ))

    return all_nodes, all_edges
```

- [ ] **Step 2: Refactor _parse_notebook to use _parse_notebook_cells**

Replace the body of `_parse_notebook` (lines 430-554) with:

```python
def _parse_notebook(
    self, path: Path, source: bytes,
) -> tuple[list[NodeInfo], list[EdgeInfo]]:
    """Parse a Jupyter notebook by extracting code cells."""
    try:
        nb = json.loads(source)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return [], []

    # Determine kernel language
    kernel_lang = (
        nb.get("metadata", {}).get("kernelspec", {}).get("language")
        or nb.get("metadata", {}).get("language_info", {}).get("name")
        or "python"
    ).lower()

    # Only parse supported languages
    supported = {"python", "r"}
    if kernel_lang not in supported:
        return [], []

    # Build CellInfo list from code cells
    cells: list[CellInfo] = []
    magic_lang_map = {
        "%python": "python",
        "%sql": "sql",
        "%r": "r",
    }
    skip_magics = {"%scala", "%md", "%sh"}

    for cell_idx, cell in enumerate(nb.get("cells", [])):
        if cell.get("cell_type") != "code":
            continue
        lines = cell.get("source", [])
        if isinstance(lines, str):
            lines = lines.splitlines(keepends=True)
        if not lines:
            continue

        # Check first line for language-switching magic
        first_line = lines[0].strip()
        cell_lang = kernel_lang
        cell_lines = lines

        for magic, lang in magic_lang_map.items():
            if first_line == magic or first_line.startswith(magic + " "):
                cell_lang = lang
                cell_lines = lines[1:]  # strip magic line
                break
        else:
            # Check for skip magics
            for skip in skip_magics:
                if first_line == skip or first_line.startswith(skip + " "):
                    cell_lines = []
                    break

        # Filter %pip, ! lines from Python/R content (not SQL)
        if cell_lang in ("python", "r"):
            filtered = [
                ln for ln in cell_lines
                if not ln.lstrip().startswith(("%", "!"))
            ]
        else:
            filtered = cell_lines
        if not filtered:
            continue

        cell_source = "".join(filtered)
        cells.append(CellInfo(index=cell_idx, language=cell_lang, source=cell_source))

    if not cells:
        file_path_str = str(path)
        return [NodeInfo(
            kind="File",
            name=file_path_str,
            file_path=file_path_str,
            line_start=1,
            line_end=1,
            language=kernel_lang,
            is_test=_is_test_file(file_path_str),
        )], []

    return self._parse_notebook_cells(path, cells, kernel_lang)
```

- [ ] **Step 3: Run existing notebook tests to verify no regressions**

Run: `uv run pytest tests/test_notebook.py::TestNotebookParsing -v`
Expected: All 12 existing tests PASS

- [ ] **Step 4: Commit**

```bash
git add code_review_graph/parser.py
git commit -m "refactor(parser): extract shared _parse_notebook_cells method"
```

---

## Task 4: Multi-language .ipynb fixture and tests

**Files:**
- Create: `tests/fixtures/sample_databricks_notebook.ipynb`
- Modify: `tests/test_notebook.py`

- [ ] **Step 1: Create Databricks .ipynb fixture**

Create `tests/fixtures/sample_databricks_notebook.ipynb` with this JSON content:

```json
{
  "cells": [
    {
      "cell_type": "markdown",
      "source": ["# Databricks Notebook"],
      "metadata": {}
    },
    {
      "cell_type": "code",
      "source": ["%python\n", "def transform_data(df):\n", "    return df.dropna()\n"],
      "metadata": {},
      "outputs": []
    },
    {
      "cell_type": "code",
      "source": ["%sql\n", "SELECT * FROM catalog.schema.raw_data\n", "JOIN catalog.schema.lookup ON raw_data.id = lookup.id\n"],
      "metadata": {},
      "outputs": []
    },
    {
      "cell_type": "code",
      "source": ["%r\n", "clean_data <- function(x) {\n", "  na.omit(x)\n", "}\n"],
      "metadata": {},
      "outputs": []
    },
    {
      "cell_type": "code",
      "source": ["%scala\n", "val x = 1\n"],
      "metadata": {},
      "outputs": []
    },
    {
      "cell_type": "code",
      "source": ["%md\n", "## Results section\n"],
      "metadata": {},
      "outputs": []
    },
    {
      "cell_type": "code",
      "source": ["def process_results(data):\n", "    result = transform_data(data)\n", "    return result\n"],
      "metadata": {},
      "outputs": []
    },
    {
      "cell_type": "code",
      "source": ["%sql\n", "CREATE TABLE catalog.schema.output AS SELECT * FROM catalog.schema.raw_data\n"],
      "metadata": {},
      "outputs": []
    }
  ],
  "metadata": {
    "kernelspec": {
      "language": "python",
      "display_name": "Python 3"
    }
  },
  "nbformat": 4,
  "nbformat_minor": 5
}
```

- [ ] **Step 2: Write TestDatabricksNotebookParsing tests**

Add to `tests/test_notebook.py`:

```python
class TestDatabricksNotebookParsing:
    def setup_method(self):
        self.parser = CodeParser()
        self.nodes, self.edges = self.parser.parse_file(
            FIXTURES / "sample_databricks_notebook.ipynb",
        )

    def test_parses_python_functions_from_magic(self):
        funcs = [n for n in self.nodes if n.kind == "Function"]
        names = {f.name for f in funcs}
        assert "transform_data" in names
        assert "process_results" in names

    def test_extracts_sql_table_references(self):
        imports = [e for e in self.edges if e.kind == "IMPORTS_FROM"]
        targets = {e.target for e in imports}
        assert "catalog.schema.raw_data" in targets
        assert "catalog.schema.lookup" in targets
        assert "catalog.schema.output" in targets

    def test_skips_scala_cells(self):
        # No nodes from scala cell
        names = {n.name for n in self.nodes if n.kind == "Function"}
        assert "x" not in names  # scala val should not appear

    def test_skips_md_cells(self):
        # md cell should not produce nodes
        func_count = len([n for n in self.nodes if n.kind == "Function"])
        assert func_count == 2  # transform_data + process_results

    def test_default_language_for_unmagicked_cell(self):
        """Cell 6 has no magic prefix — should use kernel default (python)."""
        funcs = {n.name: n for n in self.nodes if n.kind == "Function"}
        assert "process_results" in funcs

    def test_cell_index_tracking(self):
        funcs = {n.name: n for n in self.nodes if n.kind == "Function"}
        # transform_data is in cell index 1 (2nd cell, 0-based)
        assert funcs["transform_data"].extra.get("cell_index") == 1
        # process_results is in cell index 6 (7th cell, 0-based)
        assert funcs["process_results"].extra.get("cell_index") == 6

    def test_cross_cell_python_calls(self):
        calls = [e for e in self.edges if e.kind == "CALLS"]
        targets = {e.target.split("::")[-1] for e in calls}
        assert "transform_data" in targets
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_notebook.py::TestDatabricksNotebookParsing -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/sample_databricks_notebook.ipynb tests/test_notebook.py
git commit -m "test: add Databricks multi-language .ipynb tests"
```

---

## Task 5: Databricks .py notebook detection and parsing

**Files:**
- Modify: `code_review_graph/parser.py:246-266` (parse_bytes) and add new method
- Create: `tests/fixtures/sample_databricks_export.py`
- Modify: `tests/test_notebook.py`

- [ ] **Step 1: Create Databricks .py export fixture**

Create `tests/fixtures/sample_databricks_export.py`:

```python
# Databricks notebook source
import os
from pathlib import Path

def load_config():
    return {"env": os.getenv("ENV", "dev")}

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT * FROM bronze.events
# MAGIC JOIN silver.users ON events.user_id = users.id

# COMMAND ----------

# MAGIC %r
# MAGIC summarize_data <- function(df) {
# MAGIC   summary(df)
# MAGIC }

# COMMAND ----------

# MAGIC %md
# MAGIC ## Analysis Notes
# MAGIC This section documents the analysis.

# COMMAND ----------

def process_events(config):
    path = Path(config["env"])
    return load_config()

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE TABLE gold.summary AS SELECT * FROM silver.processed
```

- [ ] **Step 2: Write TestDatabricksPyNotebook tests**

Add to `tests/test_notebook.py`:

```python
class TestDatabricksPyNotebook:
    def setup_method(self):
        self.parser = CodeParser()
        self.nodes, self.edges = self.parser.parse_file(
            FIXTURES / "sample_databricks_export.py",
        )

    def test_detects_databricks_header(self):
        """Should parse as notebook, not regular Python."""
        file_node = [n for n in self.nodes if n.kind == "File"][0]
        assert file_node.extra.get("notebook_format") == "databricks_py"

    def test_parses_python_functions(self):
        funcs = [n for n in self.nodes if n.kind == "Function"]
        names = {f.name for f in funcs}
        assert "load_config" in names
        assert "process_events" in names

    def test_extracts_sql_tables(self):
        imports = [e for e in self.edges if e.kind == "IMPORTS_FROM"]
        targets = {e.target for e in imports}
        assert "bronze.events" in targets
        assert "silver.users" in targets
        assert "gold.summary" in targets
        assert "silver.processed" in targets

    def test_skips_magic_md_cells(self):
        # No nodes from md cell
        funcs = [n for n in self.nodes if n.kind == "Function"]
        names = {f.name for f in funcs}
        assert len(names) == 2  # load_config + process_events

    def test_cell_index_tracking(self):
        funcs = {n.name: n for n in self.nodes if n.kind == "Function"}
        # load_config is in cell 0 (first cell after header)
        assert funcs["load_config"].extra.get("cell_index") == 0
        # process_events is in cell 4 (5th cell, 0-based)
        assert funcs["process_events"].extra.get("cell_index") == 4

    def test_python_imports(self):
        imports = [
            e for e in self.edges
            if e.kind == "IMPORTS_FROM" and e.target in ("os", "pathlib")
        ]
        targets = {e.target for e in imports}
        assert "os" in targets
        assert "pathlib" in targets

    def test_cross_cell_calls(self):
        calls = [e for e in self.edges if e.kind == "CALLS"]
        targets = {e.target.split("::")[-1] for e in calls}
        assert "load_config" in targets

    def test_regular_py_not_affected(self):
        """A regular .py file (no header) should parse normally."""
        source = b"def hello():\n    return 'hi'\n"
        nodes, edges = self.parser.parse_bytes(Path("regular.py"), source)
        funcs = [n for n in nodes if n.kind == "Function"]
        assert len(funcs) == 1
        assert funcs[0].name == "hello"
        # No notebook_format extra
        file_node = [n for n in nodes if n.kind == "File"][0]
        assert "notebook_format" not in file_node.extra
```

- [ ] **Step 3: Add _parse_databricks_py_notebook method**

Add to `CodeParser` class in `code_review_graph/parser.py`, after `_parse_notebook_cells`:

```python
def _parse_databricks_py_notebook(
    self, path: Path, source: bytes,
) -> tuple[list[NodeInfo], list[EdgeInfo]]:
    """Parse a Databricks .py notebook export."""
    text = source.decode("utf-8", errors="replace")

    # Strip the header line
    lines = text.split("\n")
    if lines and lines[0].strip() == "# Databricks notebook source":
        lines = lines[1:]

    # Split on COMMAND delimiters
    cell_chunks: list[list[str]] = [[]]
    for line in lines:
        if re.match(r"^# COMMAND\s*-+\s*$", line):
            cell_chunks.append([])
        else:
            cell_chunks[-1].append(line)

    # Classify each cell
    cells: list[CellInfo] = []
    magic_lang_map = {
        "# MAGIC %sql": "sql",
        "# MAGIC %r": "r",
    }
    skip_prefixes = ("# MAGIC %md", "# MAGIC %sh")

    for cell_idx, chunk in enumerate(cell_chunks):
        non_empty = [ln for ln in chunk if ln.strip()]
        if not non_empty:
            continue

        # Check if all non-empty lines share a MAGIC prefix
        cell_lang = None
        for prefix, lang in magic_lang_map.items():
            if all(ln.startswith(prefix) for ln in non_empty):
                cell_lang = lang
                break

        if cell_lang:
            # Strip "# MAGIC " prefix (8 chars) from each line
            stripped = [
                ln[8:] if ln.startswith("# MAGIC ") else ln
                for ln in chunk
            ]
            cell_source = "\n".join(stripped)
            cells.append(CellInfo(index=cell_idx, language=cell_lang, source=cell_source))
            continue

        # Check for skip prefixes
        if all(
            ln.startswith(skip_prefixes) for ln in non_empty
        ):
            continue

        # Default: Python cell (mixed or no MAGIC)
        # Filter out any stray MAGIC lines
        py_lines = [ln for ln in chunk if not ln.startswith("# MAGIC ")]
        cell_source = "\n".join(py_lines)
        cells.append(CellInfo(index=cell_idx, language="python", source=cell_source))

    if not cells:
        file_path_str = str(path)
        file_node = NodeInfo(
            kind="File",
            name=file_path_str,
            file_path=file_path_str,
            line_start=1,
            line_end=1,
            language="python",
            is_test=_is_test_file(file_path_str),
        )
        file_node.extra["notebook_format"] = "databricks_py"
        return [file_node], []

    nodes, edges = self._parse_notebook_cells(path, cells, "python")

    # Tag File node with notebook_format
    for node in nodes:
        if node.kind == "File":
            node.extra["notebook_format"] = "databricks_py"
            break

    return nodes, edges
```

- [ ] **Step 4: Add Databricks .py detection in parse_bytes**

In `code_review_graph/parser.py`, modify `parse_bytes` (around line 263). Add after the notebook check and before the generic parser:

```python
# Databricks .py notebook exports
if language == "python" and source.startswith(b"# Databricks notebook source\n"):
    return self._parse_databricks_py_notebook(path, source)
```

The full `parse_bytes` routing section should look like:

```python
# Vue SFCs
if language == "vue":
    return self._parse_vue(path, source)

# Jupyter notebooks
if language == "notebook":
    return self._parse_notebook(path, source)

# Databricks .py notebook exports
if language == "python" and source.startswith(b"# Databricks notebook source\n"):
    return self._parse_databricks_py_notebook(path, source)

parser = self._get_parser(language)
```

- [ ] **Step 5: Run all Databricks .py tests**

Run: `uv run pytest tests/test_notebook.py::TestDatabricksPyNotebook -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add code_review_graph/parser.py tests/fixtures/sample_databricks_export.py tests/test_notebook.py
git commit -m "feat(parser): add Databricks .py notebook export parsing"
```

---

## Task 6: R-kernel notebook tests

**Files:**
- Modify: `tests/test_notebook.py`

Note: These tests require R parser mappings from PR #43. If PR #43 hasn't merged yet, mark tests with `pytest.mark.skipif` or expect them to fail. Once R support is available, they should pass.

- [ ] **Step 1: Write R-kernel notebook tests**

Add to `tests/test_notebook.py`:

```python
class TestRKernelNotebook:
    def setup_method(self):
        self.parser = CodeParser()
        nb = {
            "cells": [
                {
                    "cell_type": "code",
                    "source": [
                        "library(dplyr)\n",
                    ],
                    "outputs": [],
                },
                {
                    "cell_type": "code",
                    "source": [
                        "clean_data <- function(df) {\n",
                        "  df %>% filter(!is.na(value))\n",
                        "}\n",
                    ],
                    "outputs": [],
                },
            ],
            "metadata": {"kernelspec": {"language": "r"}},
            "nbformat": 4,
        }
        source = json.dumps(nb).encode("utf-8")
        self.nodes, self.edges = self.parser.parse_bytes(
            Path("analysis.ipynb"), source,
        )

    def test_r_kernel_not_skipped(self):
        """R-kernel notebooks should now be parsed, not skipped."""
        assert len(self.nodes) >= 1
        file_node = [n for n in self.nodes if n.kind == "File"][0]
        assert file_node.language == "r"

    def test_r_kernel_detects_functions(self):
        funcs = [n for n in self.nodes if n.kind == "Function"]
        names = {f.name for f in funcs}
        assert "clean_data" in names

    def test_r_kernel_detects_imports(self):
        imports = [e for e in self.edges if e.kind == "IMPORTS_FROM"]
        targets = {e.target for e in imports}
        assert "dplyr" in targets
```

- [ ] **Step 2: Update test_non_python_kernel**

The existing `test_non_python_kernel` in `TestNotebookParsing` tests that R-kernel notebooks return empty. Update it since R kernels are now supported:

Find the test at line 86-99 in `tests/test_notebook.py` and replace it with:

```python
def test_unsupported_kernel(self):
    nb = {
        "cells": [
            {"cell_type": "code", "source": ["val x = 1"], "outputs": []},
        ],
        "metadata": {"kernelspec": {"language": "scala"}},
        "nbformat": 4,
    }
    source = json.dumps(nb).encode("utf-8")
    nodes, edges = self.parser.parse_bytes(
        Path("scala_notebook.ipynb"), source,
    )
    assert nodes == []
    assert edges == []
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_notebook.py::TestRKernelNotebook tests/test_notebook.py::TestNotebookParsing::test_unsupported_kernel -v`
Expected: R kernel tests PASS if R parser available; unsupported kernel test PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_notebook.py
git commit -m "test: add R-kernel notebook and unsupported kernel tests"
```

---

## Task 7: Negative and edge case tests

**Files:**
- Modify: `tests/test_notebook.py`

- [ ] **Step 1: Write negative test cases**

Add to `tests/test_notebook.py`:

```python
class TestNotebookEdgeCases:
    def setup_method(self):
        self.parser = CodeParser()

    def test_databricks_header_not_on_line_1(self):
        """Header on line 2 should be treated as regular Python."""
        source = b"# comment\n# Databricks notebook source\ndef foo(): pass\n"
        nodes, edges = self.parser.parse_bytes(Path("not_db.py"), source)
        file_node = [n for n in nodes if n.kind == "File"][0]
        assert "notebook_format" not in file_node.extra

    def test_databricks_py_no_command_delimiters(self):
        """Header present but no COMMAND delimiters — single Python cell."""
        source = b"# Databricks notebook source\ndef foo():\n    return 1\n"
        nodes, edges = self.parser.parse_bytes(Path("single_cell.py"), source)
        funcs = [n for n in nodes if n.kind == "Function"]
        assert len(funcs) == 1
        assert funcs[0].name == "foo"

    def test_empty_databricks_cells(self):
        """Cells with only magic/shell lines should be skipped."""
        nb = {
            "cells": [
                {"cell_type": "code", "source": ["%pip install foo\n"], "outputs": []},
                {"cell_type": "code", "source": ["!ls\n"], "outputs": []},
                {"cell_type": "code", "source": ["def real(): pass\n"], "outputs": []},
            ],
            "metadata": {"kernelspec": {"language": "python"}},
            "nbformat": 4,
        }
        source = json.dumps(nb).encode("utf-8")
        nodes, edges = self.parser.parse_bytes(Path("sparse.ipynb"), source)
        funcs = [n for n in nodes if n.kind == "Function"]
        assert len(funcs) == 1
        assert funcs[0].name == "real"

    def test_sql_cell_no_tables(self):
        """SQL cell with no table refs should produce no edges."""
        nb = {
            "cells": [
                {"cell_type": "code", "source": ["%sql\n", "SELECT 1 + 1\n"], "outputs": []},
            ],
            "metadata": {"kernelspec": {"language": "python"}},
            "nbformat": 4,
        }
        source = json.dumps(nb).encode("utf-8")
        nodes, edges = self.parser.parse_bytes(Path("no_tables.ipynb"), source)
        imports = [e for e in edges if e.kind == "IMPORTS_FROM"]
        assert imports == []

    def test_conflicting_kernel_metadata(self):
        """kernelspec.language takes precedence over language_info.name."""
        nb = {
            "cells": [
                {"cell_type": "code", "source": ["def foo(): pass\n"], "outputs": []},
            ],
            "metadata": {
                "kernelspec": {"language": "python"},
                "language_info": {"name": "r"},
            },
            "nbformat": 4,
        }
        source = json.dumps(nb).encode("utf-8")
        nodes, edges = self.parser.parse_bytes(Path("conflict.ipynb"), source)
        file_node = [n for n in nodes if n.kind == "File"][0]
        assert file_node.language == "python"
```

- [ ] **Step 2: Run edge case tests**

Run: `uv run pytest tests/test_notebook.py::TestNotebookEdgeCases -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_notebook.py
git commit -m "test: add notebook edge case and negative tests"
```

---

## Task 8: Full test suite and final verification

**Files:** None (verification only)

- [ ] **Step 1: Run full notebook test file**

Run: `uv run pytest tests/test_notebook.py -v`
Expected: All tests PASS

- [ ] **Step 2: Run full project test suite**

Run: `uv run pytest tests/ --tb=short -q`
Expected: All tests PASS (194+ existing + new tests)

- [ ] **Step 3: Run linter**

Run: `uv run ruff check code_review_graph/parser.py`
Expected: No errors

- [ ] **Step 4: Run type checker**

Run: `uv run mypy code_review_graph/parser.py --ignore-missing-imports --no-strict-optional`
Expected: No errors

- [ ] **Step 5: Commit any lint fixes if needed**

```bash
git add -u
git commit -m "fix: address lint/type issues"
```
