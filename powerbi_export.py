"""Convert a generated Apache Ossie semantic model (already a Python dict,
as produced by ``ossie_builder.build_semantic_model``) into a Power BI /
Analysis Services Tabular semantic model definition -- without needing
Power BI Desktop or any other desktop application.

Two artifact families are produced, both are the *real* file formats Power
BI/Fabric semantic models are defined in under the hood:

- **TMSL** (Tabular Model Scripting Language) -- a single ``model.bim``
  JSON document. This is the classic Analysis Services Tabular format and
  is directly consumable by tools such as Tabular Editor, SSDT, or the AS
  engine's deployment APIs.
- **TMDL** (Tabular Model Definition Language) -- a folder of small, git
  friendly text files (``model.tmdl``, one file per table, a
  ``relationships.tmdl``). This is the format behind modern Power BI
  Projects (``.pbip``) and Fabric's git-integrated semantic models: you can
  open the generated folder directly in Power BI Desktop via
  *File > Open > Power BI Project*, or push it to a Fabric workspace with
  the Fabric REST API / ``fab`` CLI / Tabular Editor's command line --
  fully headless, no GUI required.

This module also includes a small, best-effort SQL -> DAX translator for
field/metric expressions, and a synthetic-data + DuckDB based "run the
metrics" preview, so the app can show illustrative computed values for the
metrics without needing a real, connected data warehouse or a live Power BI
/ Fabric workspace (which this sandboxed environment does not have).

None of this requires Power BI Desktop, Tableau Desktop, or any other GUI
application to *generate*. Actually rendering the semantic model inside the
Power BI service still requires Power BI itself somewhere downstream (it's
Microsoft's proprietary product) -- but that final step can be done
headlessly too (Fabric REST API, Tabular Editor CLI, XMLA endpoint), not
necessarily via Power BI Desktop.
"""

from __future__ import annotations

import io
import json
import random
import re
import uuid
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# ---------------------------------------------------------------------------
# Data type mapping: Ossie DataType -> Tabular Object Model dataType
# ---------------------------------------------------------------------------

_TABULAR_DATATYPE = {
    "String": "string",
    "Integer": "int64",
    "Decimal": "decimal",
    "Float": "double",
    "Boolean": "boolean",
    "Date": "dateTime",
    "Time": "dateTime",  # Tabular has no standalone time type
    "DateTime": "dateTime",
    "DateTimeTz": "dateTime",
    "Opaque": "string",
}


def map_datatype_to_tabular(ossie_datatype: Optional[str]) -> str:
    return _TABULAR_DATATYPE.get(ossie_datatype or "", "string")


def _guess_format_string(ossie_datatype: Optional[str]) -> str:
    if ossie_datatype in ("Decimal", "Float"):
        return "#,##0.00"
    if ossie_datatype == "Integer":
        return "#,##0"
    return "General"


# ---------------------------------------------------------------------------
# Best-effort ANSI SQL -> DAX translation
# ---------------------------------------------------------------------------

_DOT_REF_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b")
_COUNT_DISTINCT_RE = re.compile(r"COUNT\s*\(\s*DISTINCT\s+([^)]+)\)", re.IGNORECASE)
_NULLIF_ZERO_RE = re.compile(r"NULLIF\s*\(\s*(.+?)\s*,\s*0\s*\)", re.IGNORECASE)


def _wrap_top_level_division(expr: str) -> str:
    """Wrap a single top-level ``a / b`` as DAX's zero-safe ``DIVIDE(a, b)``.

    Leaves the expression untouched if there's no top-level division, or if
    there's more than one (ambiguous to rewrite automatically).
    """
    depth = 0
    split_at = None
    for i, ch in enumerate(expr):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "/" and depth == 0:
            if split_at is not None:
                return expr
            split_at = i
    if split_at is None:
        return expr
    left = expr[:split_at].strip()
    right = expr[split_at + 1 :].strip()
    return f"DIVIDE({left}, {right})"


def sql_to_dax(expr: str) -> str:
    """Best-effort conversion of an Ossie ANSI_SQL field/metric expression
    into a DAX expression suitable for a Tabular model measure/column.

    Handles: ``table.column`` -> ``table[column]``, ``COUNT(DISTINCT x)`` ->
    ``DISTINCTCOUNT(x)``, ``AVG(`` -> ``AVERAGE(``, ``NULLIF(x, 0)`` ->
    ``x`` (since DAX's ``DIVIDE`` is zero-safe on its own), and a single
    top-level division -> ``DIVIDE(a, b)``. Arbitrary/complex SQL may not
    translate perfectly -- always review generated DAX before deploying.
    """
    if not expr:
        return expr
    s = _DOT_REF_RE.sub(lambda m: f"{m.group(1)}[{m.group(2)}]", expr)
    s = _COUNT_DISTINCT_RE.sub(lambda m: f"DISTINCTCOUNT({m.group(1).strip()})", s)
    s = re.sub(r"\bAVG\s*\(", "AVERAGE(", s, flags=re.IGNORECASE)
    s = _NULLIF_ZERO_RE.sub(lambda m: m.group(1).strip(), s)
    s = _wrap_top_level_division(s)
    return s


def _get_ansi_expression(expression_obj: Dict[str, Any]) -> str:
    dialects = expression_obj.get("dialects", []) if expression_obj else []
    for d in dialects:
        if d.get("dialect") == "ANSI_SQL":
            return d.get("expression", "")
    if dialects:
        return dialects[0].get("expression", "")
    return ""


def _first_table_reference(expr: str, table_names) -> Optional[str]:
    """The earliest-occurring known table name referenced as ``name.`` in
    ``expr`` -- used to pick a DAX measure's "home table"."""
    best_idx, best_name = None, None
    for name in table_names:
        idx = expr.find(f"{name}.")
        if idx == -1:
            continue
        if best_idx is None or idx < best_idx:
            best_idx, best_name = idx, name
    return best_name


def _referenced_tables(expr: str, table_names) -> List[str]:
    return [name for name in table_names if re.search(rf"\b{re.escape(name)}\.", expr)]


# ---------------------------------------------------------------------------
# Power Query (M) placeholder source expression
# ---------------------------------------------------------------------------

def build_m_expression(table_name: str, source: str) -> str:
    """A best-effort, illustrative Power Query M partition source built from
    an Ossie dataset's ``source`` field (``database.schema.table`` or
    ``schema.table`` or ``table``). Meant as a starting point -- replace the
    connection details with your real data source before deploying.
    """
    parts = (source or table_name).split(".")
    if len(parts) >= 3:
        database, schema, table = parts[0], parts[1], ".".join(parts[2:])
    elif len(parts) == 2:
        database, schema, table = None, parts[0], parts[1]
    else:
        database, schema, table = None, None, parts[0]

    server_literal = database or "<YOUR_SERVER_OR_DATABASE>"
    database_literal = database or "<YOUR_DATABASE>"
    schema_literal = schema or "dbo"
    return (
        "// TODO: replace with your real data source connection details\n"
        "let\n"
        f'    Source = Sql.Database("{server_literal}", "{database_literal}"),\n'
        f'    {table}_table = Source{{[Schema="{schema_literal}",Item="{table}"]}}[Data]\n'
        "in\n"
        f"    {table}_table"
    )


def build_snowflake_m_expression(
    table_name: str,
    source: str,
    account: str,
    warehouse: str,
    role: Optional[str] = None,
) -> str:
    """A real, connector-accurate Power Query M partition source for
    Snowflake, using Power BI's native ``Snowflake.Databases`` connector
    (the same M code Power BI Desktop's Get Data > Snowflake wizard
    generates). ``source`` supplies the database/schema/table (from the
    Ossie dataset's ``source`` field, i.e. the app's "Source prefix" +
    table name) -- ``account``/``warehouse``/``role`` are your Snowflake
    connection settings, not credentials. Credentials themselves are never
    embedded here; Power BI prompts for them (username/password, SSO,
    key-pair, etc.) the first time the model connects or refreshes.
    """
    parts = (source or table_name).split(".")
    if len(parts) >= 3:
        database, schema, table = parts[0], parts[1], ".".join(parts[2:])
    elif len(parts) == 2:
        database, schema, table = "<YOUR_DATABASE>", parts[0], parts[1]
    else:
        database, schema, table = "<YOUR_DATABASE>", "<YOUR_SCHEMA>", parts[0]

    options = f', [Role="{role}"]' if role else ""
    return (
        "let\n"
        f'    Source = Snowflake.Databases("{account}", "{warehouse}"{options}),\n'
        f'    {table}_Db = Source{{[Name="{database}", Kind="Database"]}}[Data],\n'
        f'    {table}_Schema = {table}_Db{{[Name="{schema}", Kind="Schema"]}}[Data],\n'
        f'    {table}_Table = {table}_Schema{{[Name="{table}", Kind="Table"]}}[Data]\n'
        "in\n"
        f"    {table}_Table"
    )


def _build_partition_m_expression(table_name: str, source: str, data_source: Optional[Dict[str, Any]]) -> str:
    if data_source and data_source.get("type") == "snowflake":
        return build_snowflake_m_expression(
            table_name,
            source,
            account=data_source["account"],
            warehouse=data_source["warehouse"],
            role=data_source.get("role"),
        )
    return build_m_expression(table_name, source)


# ---------------------------------------------------------------------------
# TMSL (model.bim) builder
# ---------------------------------------------------------------------------

def build_tmsl_model(
    ossie_model: Dict[str, Any],
    compatibility_level: int = 1567,
    data_source: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a Tabular Model Scripting Language (``model.bim``) document
    from an Ossie semantic model dict.

    ``data_source``, if given, controls what Power Query (M) partition
    source is generated for every table's partition. Currently supported:
    ``{"type": "snowflake", "account": ..., "warehouse": ..., "role": ...}``
    (``role`` optional) -- produces real ``Snowflake.Databases(...)`` M
    code. When omitted, a generic ``Sql.Database`` placeholder is used
    instead (meant to be hand-edited before deploying).
    """
    sm = ossie_model["semantic_model"][0]
    dataset_names = [d["name"] for d in sm.get("datasets", [])]

    metrics_by_table: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    unassigned_metrics: List[Dict[str, Any]] = []
    for metric in sm.get("metrics", []):
        expr = _get_ansi_expression(metric["expression"])
        home = _first_table_reference(expr, dataset_names)
        (metrics_by_table[home] if home else unassigned_metrics).append(metric)

    tables_out: List[Dict[str, Any]] = []
    for dataset in sm.get("datasets", []):
        table_name = dataset["name"]
        columns = []
        for field in dataset.get("fields", []):
            col: Dict[str, Any] = {
                "name": field["name"],
                "dataType": map_datatype_to_tabular(field.get("datatype")),
                "sourceColumn": field["name"],
            }
            if field.get("description"):
                col["description"] = field["description"]
            columns.append(col)

        measures = []
        for metric in metrics_by_table.get(table_name, []):
            expr = _get_ansi_expression(metric["expression"])
            measure: Dict[str, Any] = {
                "name": metric["name"],
                "expression": sql_to_dax(expr),
                "formatString": _guess_format_string(metric.get("datatype")),
            }
            if metric.get("description"):
                measure["description"] = metric["description"]
            measures.append(measure)

        table_entry: Dict[str, Any] = {
            "name": table_name,
            "columns": columns,
            "partitions": [
                {
                    "name": f"{table_name}-partition",
                    "mode": "import",
                    "source": {
                        "type": "m",
                        "expression": _build_partition_m_expression(
                            table_name, dataset.get("source", table_name), data_source
                        ),
                    },
                }
            ],
        }
        if measures:
            table_entry["measures"] = measures
        tables_out.append(table_entry)

    if unassigned_metrics and tables_out:
        home_table = tables_out[0]
        home_table.setdefault("measures", [])
        for metric in unassigned_metrics:
            expr = _get_ansi_expression(metric["expression"])
            home_table["measures"].append(
                {
                    "name": metric["name"],
                    "expression": sql_to_dax(expr),
                    "formatString": _guess_format_string(metric.get("datatype")),
                }
            )

    relationships_out = []
    for rel in sm.get("relationships", []):
        # Tabular relationships are single-column; Ossie supports composite
        # keys. We use the first column pair -- composite relationships
        # aren't fully representable in a single Tabular relationship and
        # may need a surrogate/concatenated key when deploying for real.
        relationships_out.append(
            {
                "name": rel["name"],
                "fromTable": rel["from"],
                "fromColumn": rel["from_columns"][0],
                "toTable": rel["to"],
                "toColumn": rel["to_columns"][0],
                "crossFilteringBehavior": "automatic",
            }
        )

    model: Dict[str, Any] = {
        "name": sm["name"],
        "compatibilityLevel": compatibility_level,
        "model": {
            "culture": "en-US",
            "dataAccessOptions": {"legacyRedirects": True, "returnErrorValuesAsNull": True},
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "sourceQueryCulture": "en-US",
            "tables": tables_out,
            "annotations": [
                {"name": "PBI_QueryOrder", "value": json.dumps([t["name"] for t in tables_out])},
                {"name": "GeneratedBy", "value": "Ossie Semantic Model Builder (OSI YAML to Power BI TMSL)"},
            ],
        },
    }
    if relationships_out:
        model["model"]["relationships"] = relationships_out
    return model


def tmsl_to_json_str(tmsl: Dict[str, Any]) -> str:
    return json.dumps(tmsl, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# TMDL (folder-based Power BI Project) builder
# ---------------------------------------------------------------------------

def _tmdl_table_content(table: Dict[str, Any]) -> str:
    lines = [f"table {table['name']}", ""]
    for col in table["columns"]:
        lines.append(f"\tcolumn {col['name']}")
        lines.append(f"\t\tdataType: {col['dataType']}")
        lines.append(f"\t\tsourceColumn: {col['sourceColumn']}")
        if col.get("description"):
            lines.append(f"\t\t/// {col['description']}")
        lines.append("")
    for measure in table.get("measures", []):
        lines.append(f"\tmeasure {measure['name']} = {measure['expression']}")
        if measure.get("formatString"):
            lines.append(f"\t\tformatString: {measure['formatString']}")
        if measure.get("description"):
            lines.append(f"\t\t/// {measure['description']}")
        lines.append("")
    for partition in table.get("partitions", []):
        lines.append(f"\tpartition {partition['name']} = m")
        lines.append(f"\t\tmode: {partition['mode']}")
        lines.append("\t\tsource =")
        for m_line in partition["source"]["expression"].splitlines():
            lines.append(f"\t\t\t{m_line}")
        lines.append("")
    return "\n".join(lines)


def _tmdl_relationships_content(relationships: List[Dict[str, Any]]) -> str:
    lines = []
    for rel in relationships:
        lines.append(f"relationship {rel['name']}")
        lines.append(f"\tfromColumn: {rel['fromTable']}.{rel['fromColumn']}")
        lines.append(f"\ttoColumn: {rel['toTable']}.{rel['toColumn']}")
        lines.append("")
    return "\n".join(lines)


def _tmdl_model_content(tmsl: Dict[str, Any]) -> str:
    m = tmsl["model"]
    lines = [f"model {tmsl['name']}", f"\tculture: {m.get('culture', 'en-US')}", ""]
    for t in m["tables"]:
        lines.append(f"ref table {t['name']}")
    if m.get("relationships"):
        lines.append("")
        for r in m["relationships"]:
            lines.append(f"ref relationship {r['name']}")
    return "\n".join(lines)


def build_tmdl_files(ossie_model: Dict[str, Any], data_source: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Returns ``{relative_path: file_text}`` for a TMDL semantic model
    definition (the format behind modern Power BI Projects / Fabric
    git-integrated semantic models)."""
    tmsl = build_tmsl_model(ossie_model, data_source=data_source)
    files = {"definition/model.tmdl": _tmdl_model_content(tmsl)}
    for t in tmsl["model"]["tables"]:
        files[f"definition/tables/{t['name']}.tmdl"] = _tmdl_table_content(t)
    if tmsl["model"].get("relationships"):
        files["definition/relationships.tmdl"] = _tmdl_relationships_content(tmsl["model"]["relationships"])
    return files


def _safe_project_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9 _-]", "_", name).strip() or "semantic_model"


def build_semantic_model_project_files(
    ossie_model: Dict[str, Any], project_name: str, data_source: Optional[Dict[str, Any]] = None
) -> Dict[str, bytes]:
    """The full file set for a Fabric/Power BI ``<name>.SemanticModel``
    folder: ``.platform`` + ``definition.pbism`` + TMDL ``definition/``
    files, plus the raw ``model.bim`` (TMSL) for tools that prefer JSON.
    This folder can be committed to git for Fabric's git integration,
    deployed headlessly via the Fabric REST API / ``fab`` CLI / Tabular
    Editor CLI, or opened directly in Power BI Desktop.
    """
    safe_name = _safe_project_name(project_name)
    root = f"{safe_name}.SemanticModel"
    files: Dict[str, bytes] = {}

    platform_doc = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "SemanticModel", "displayName": safe_name},
        "config": {"version": "2.0", "logicalId": str(uuid.uuid4())},
    }
    files[f"{root}/.platform"] = json.dumps(platform_doc, indent=2).encode("utf-8")
    files[f"{root}/definition.pbism"] = json.dumps({"version": "4.2", "settings": {}}, indent=2).encode("utf-8")

    for rel_path, content in build_tmdl_files(ossie_model, data_source=data_source).items():
        files[f"{root}/{rel_path}"] = content.encode("utf-8")

    tmsl = build_tmsl_model(ossie_model, data_source=data_source)
    files[f"{root}/model.bim"] = tmsl_to_json_str(tmsl).encode("utf-8")

    readme = (
        f"# {safe_name} -- Power BI semantic model (generated from Apache Ossie)\n\n"
        "This folder is a Fabric/Power BI \"semantic model as code\" project:\n\n"
        "- Open it directly in Power BI Desktop via File > Open > Power BI Project.\n"
        "- Or commit it to git and sync it into a Fabric workspace (git integration).\n"
        "- Or deploy it headlessly with the Tabular Editor CLI or the Fabric REST API --\n"
        "  no desktop application is required for that path.\n\n"
        "`model.bim` contains the same model as plain TMSL JSON, for tools that prefer\n"
        "a single JSON file over the TMDL folder layout.\n\n"
        "Before deploying: review the placeholder Power Query (M) source expressions in\n"
        "definition/tables/*.tmdl and point them at your real data source.\n"
    )
    files[f"{root}/README.md"] = readme.encode("utf-8")

    return files


def build_pbip_zip_bytes(
    ossie_model: Dict[str, Any], project_name: str, data_source: Optional[Dict[str, Any]] = None
) -> bytes:
    files = build_semantic_model_project_files(ossie_model, project_name, data_source=data_source)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


@dataclass
class PowerBiExport:
    tmsl: Dict[str, Any]
    tmsl_json: str
    tmdl_files: Dict[str, str]
    zip_bytes: bytes


def convert_to_powerbi(
    ossie_model: Dict[str, Any], project_name: str, data_source: Optional[Dict[str, Any]] = None
) -> PowerBiExport:
    tmsl = build_tmsl_model(ossie_model, data_source=data_source)
    return PowerBiExport(
        tmsl=tmsl,
        tmsl_json=tmsl_to_json_str(tmsl),
        tmdl_files=build_tmdl_files(ossie_model, data_source=data_source),
        zip_bytes=build_pbip_zip_bytes(ossie_model, project_name, data_source=data_source),
    )


# ---------------------------------------------------------------------------
# Synthetic data + local metric preview (no live data source required)
# ---------------------------------------------------------------------------

def _synthetic_column(datatype: str, n: int, rng: random.Random, name: str) -> List[Any]:
    if datatype == "Integer":
        return [rng.randint(1, 1000) for _ in range(n)]
    if datatype in ("Decimal", "Float"):
        return [round(rng.uniform(10, 100000), 2) for _ in range(n)]
    if datatype == "Boolean":
        return [rng.choice([True, False]) for _ in range(n)]
    if datatype in ("Date", "DateTime", "DateTimeTz"):
        base = date(2025, 1, 1)
        return [base + timedelta(days=rng.randint(0, 600)) for _ in range(n)]
    if datatype == "Time":
        return [f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:00" for _ in range(n)]
    return [f"{name}_{rng.randint(1, 50)}" for _ in range(n)]


def generate_synthetic_data(ossie_model: Dict[str, Any], n_rows: int = 200, seed: int = 42) -> Dict[str, pd.DataFrame]:
    """Generates small, random-but-typed sample dataframes matching each
    dataset's fields, purely so metric expressions can be *previewed*
    end-to-end without a real, connected data source. Values are random and
    carry no business meaning.
    """
    rng = random.Random(seed)
    sm = ossie_model["semantic_model"][0]
    data: Dict[str, pd.DataFrame] = {}
    for dataset in sm.get("datasets", []):
        cols = {
            field["name"]: _synthetic_column(field.get("datatype", "String"), n_rows, rng, field["name"])
            for field in dataset.get("fields", [])
        }
        data[dataset["name"]] = pd.DataFrame(cols)
    return data


def _build_from_clause(tables: List[str], relationships: List[Dict[str, Any]]) -> Optional[str]:
    if not tables:
        return None
    if len(tables) == 1:
        return tables[0]
    remaining = set(tables)
    joined = {tables[0]}
    clause = tables[0]
    progress = True
    while joined != remaining and progress:
        progress = False
        for rel in relationships:
            a, b = rel["from"], rel["to"]
            if a in joined and b in remaining and b not in joined:
                clause += f" JOIN {b} ON {a}.{rel['from_columns'][0]} = {b}.{rel['to_columns'][0]}"
                joined.add(b)
                progress = True
            elif b in joined and a in remaining and a not in joined:
                clause += f" JOIN {a} ON {a}.{rel['from_columns'][0]} = {b}.{rel['to_columns'][0]}"
                joined.add(a)
                progress = True
    return clause if joined == remaining else None


def evaluate_metrics(ossie_model: Dict[str, Any], synthetic_data: Dict[str, pd.DataFrame]) -> List[Dict[str, Any]]:
    """Runs each metric's original ANSI_SQL expression against the
    synthetic data using an in-memory DuckDB engine, purely to demonstrate
    that the metric logic executes and to show an illustrative value.
    These are NOT real business results -- there is no live Power BI
    service or data warehouse connected in this environment.
    """
    import duckdb

    sm = ossie_model["semantic_model"][0]
    table_names = list(synthetic_data.keys())
    relationships = sm.get("relationships", [])
    results: List[Dict[str, Any]] = []

    con = duckdb.connect(database=":memory:")
    try:
        for name, df in synthetic_data.items():
            con.register(name, df)
        for metric in sm.get("metrics", []):
            expr = _get_ansi_expression(metric["expression"])
            entry: Dict[str, Any] = {
                "name": metric["name"],
                "sql_expression": expr,
                "dax_expression": sql_to_dax(expr),
                "value": None,
                "error": None,
            }
            refs = _referenced_tables(expr, table_names)
            from_clause = _build_from_clause(refs, relationships) if refs else None
            if not from_clause:
                entry["error"] = "Could not resolve a join path across the referenced tables for this preview."
            else:
                try:
                    row = con.sql(f"SELECT {expr} AS metric_value FROM {from_clause}").fetchone()
                    entry["value"] = row[0] if row else None
                except Exception as e:  # noqa: BLE001
                    entry["error"] = str(e)
            results.append(entry)
    finally:
        con.close()
    return results
