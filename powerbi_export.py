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
  ``relationships.tmdl``). This is the format behind Fabric's
  git-integrated semantic models: push the generated folder to a Fabric
  workspace's connected git repo, or deploy it with the Fabric REST API /
  ``fab`` CLI -- fully headless, no GUI required.

``build_pbip_zip_bytes`` bundles the semantic model together with a
minimal, blank ``.Report`` scaffold and a top-level ``.pbip`` manifest, so
the download is a genuine, directly-openable Power BI Project (*File >
Open > Power BI Project* in Desktop) with no third-party tool required
just to open it. That blank report is a best-effort scaffold generated
without a real Power BI Desktop available here to verify against; if it
doesn't open cleanly, the ``.SemanticModel`` folder still works on its own
via Tabular Editor or the Fabric REST API / git integration (see the
README).

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


def physical_source_column(field: Dict[str, Any]) -> str:
    """Return the Power Query / warehouse column name to bind as Tabular
    ``sourceColumn``.

    Prefers ``description_from_source_system`` from the field's COMMON
    ``custom_extensions`` when present -- in this app's metadata spreadsheet
    that column holds the *physical* source-system column name (e.g.
    ``CLNT_SK``) while ``Column Title`` / Ossie ``fields[].name`` is the
    logical catalog name (e.g. ``CLIENT_ID``). Power BI Import mode maps
    query columns by exact ``sourceColumn`` match; binding the logical name
    when Snowflake returns the physical name yields zero matched columns and
    Desktop's "This query doesn't have any columns with supported data
    types" load error. Falls back to the Ossie field name when no physical
    name is recorded.
    """
    for ext in field.get("custom_extensions") or []:
        if ext.get("vendor_name") != "COMMON":
            continue
        raw = ext.get("data") or "{}"
        try:
            data = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (TypeError, json.JSONDecodeError):
            data = {}
        physical = (data.get("description_from_source_system") or "").strip()
        if physical:
            return physical
    return field["name"]


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
                "sourceColumn": physical_source_column(field),
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

def _tmdl_comment_line(text: str) -> str:
    """A single-line ``///`` doc-comment -- TMDL comments don't span
    multiple lines, so any embedded newlines (e.g. from a multi-paragraph
    description typed into a spreadsheet cell) are collapsed first."""
    return " ".join(text.split())


def _tmdl_table_content(table: Dict[str, Any]) -> str:
    lines = [f"table {table['name']}", ""]
    for col in table["columns"]:
        lines.append(f"\tcolumn {col['name']}")
        lines.append(f"\t\tdataType: {col['dataType']}")
        lines.append(f"\t\tsourceColumn: {col['sourceColumn']}")
        if col.get("description"):
            lines.append(f"\t\t/// {_tmdl_comment_line(col['description'])}")
        lines.append("")
    for measure in table.get("measures", []):
        lines.append(f"\tmeasure {measure['name']} = {measure['expression']}")
        if measure.get("formatString"):
            lines.append(f"\t\tformatString: {measure['formatString']}")
        if measure.get("description"):
            lines.append(f"\t\t/// {_tmdl_comment_line(measure['description'])}")
        lines.append("")
    for partition in table.get("partitions", []):
        lines.append(f"\tpartition {partition['name']} = m")
        lines.append(f"\t\tmode: {partition['mode']}")
        lines.append("\t\tsource =")
        # TMDL is strictly indentation-sensitive (tabs, one level per
        # nesting depth) and rejects mixed tabs/spaces with a parse error
        # ("Invalid indentation was detected") in Power BI Desktop. The M
        # expression text below has its own human-readable 4-space
        # indentation (see build_m_expression/build_snowflake_m_expression)
        # which is meaningless to the M language itself -- strip it before
        # re-indenting with tabs so every embedded line gets exactly one
        # consistent (tab-only) indentation, not tabs-then-leftover-spaces.
        for m_line in partition["source"]["expression"].splitlines():
            stripped = m_line.strip()
            lines.append(f"\t\t\t{stripped}" if stripped else "")
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


def _tmdl_database_content(tmsl: Dict[str, Any]) -> str:
    """The ``database.tmdl`` file -- REQUIRED for every TMDL semantic
    model, and must be the file that carries ``compatibilityLevel``: a
    bare ``compatibilityLevel:`` property with no enclosing ``database``
    object declaration is invalid TMDL (Power BI Desktop rejects it,
    historically surfacing as a generic ``InvalidLineType`` /
    "unexpected line type" parse error rather than a specific "missing
    database.tmdl" message).
    """
    return "\n".join(
        [
            "database",
            f"\tcompatibilityLevel: {tmsl['compatibilityLevel']}",
            "",
        ]
    )


def _tmdl_model_content(tmsl: Dict[str, Any]) -> str:
    m = tmsl["model"]
    lines = [
        f"model {tmsl['name']}",
        f"\tculture: {m.get('culture', 'en-US')}",
        f"\tdefaultPowerBIDataSourceVersion: {m.get('defaultPowerBIDataSourceVersion', 'powerBI_V3')}",
        f"\tsourceQueryCulture: {m.get('sourceQueryCulture', 'en-US')}",
        "",
    ]
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
    git-integrated semantic models). Includes the required
    ``database.tmdl`` (carrying ``compatibilityLevel``) alongside
    ``model.tmdl``, one file per table, and ``relationships.tmdl``.
    """
    tmsl = build_tmsl_model(ossie_model, data_source=data_source)
    files = {
        "definition/database.tmdl": _tmdl_database_content(tmsl),
        "definition/model.tmdl": _tmdl_model_content(tmsl),
    }
    for t in tmsl["model"]["tables"]:
        files[f"definition/tables/{t['name']}.tmdl"] = _tmdl_table_content(t)
    if tmsl["model"].get("relationships"):
        files["definition/relationships.tmdl"] = _tmdl_relationships_content(tmsl["model"]["relationships"])
    return files


def _safe_project_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9 _-]", "_", name).strip() or "semantic_model"


def build_pbism_bytes() -> bytes:
    """The minimal ``definition.pbism`` part required by every Power BI /
    Fabric semantic model definition (TMDL or TMSL)."""
    return json.dumps({"version": "4.2", "settings": {}}, indent=2).encode("utf-8")


def build_platform_bytes(display_name: str) -> bytes:
    """The ``.platform`` part Fabric uses for git integration / item
    metadata (display name, item type, a fresh logical id)."""
    platform_doc = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "SemanticModel", "displayName": display_name},
        "config": {"version": "2.0", "logicalId": str(uuid.uuid4())},
    }
    return json.dumps(platform_doc, indent=2).encode("utf-8")


def build_semantic_model_project_files(
    ossie_model: Dict[str, Any], project_name: str, data_source: Optional[Dict[str, Any]] = None
) -> Dict[str, bytes]:
    """The full file set for a Fabric/Power BI ``<name>.SemanticModel``
    folder used in the downloadable, directly-openable ``.pbip`` project:
    ``.platform`` + ``definition.pbism`` + ``model.bim`` (TMSL/JSON).

    Deliberately **TMSL only, no TMDL** ``definition/`` folder here: per
    Microsoft's own docs, ``model.bim`` and a TMDL ``definition/`` folder
    are *mutually exclusive* representations of the same semantic model --
    shipping both in the same folder is itself invalid and was previously
    causing generic, hard-to-diagnose TMDL parse errors ("Invalid line
    type") when Power BI Desktop opened the project. TMSL is plain JSON
    with no indentation sensitivity at all, so it's the safer format to
    ship for an export this app can't verify against a real Power BI
    Desktop. TMDL is still generated separately (see ``build_tmdl_files``)
    for the app's own "TMDL files" preview tab and for headless Fabric
    REST API deployment (``fabric_deploy.py``), which uses TMDL alone --
    never mixed with ``model.bim``.
    """
    safe_name = _safe_project_name(project_name)
    root = f"{safe_name}.SemanticModel"

    tmsl = build_tmsl_model(ossie_model, data_source=data_source)
    files: Dict[str, bytes] = {
        f"{root}/.platform": build_platform_bytes(safe_name),
        f"{root}/definition.pbism": build_pbism_bytes(),
        f"{root}/model.bim": tmsl_to_json_str(tmsl).encode("utf-8"),
    }

    readme = (
        f"# {safe_name} -- Power BI semantic model (generated from Apache Ossie)\n\n"
        "This is the semantic model half of the Power BI Project in this zip --\n"
        "`.platform` + `definition.pbism` + `model.bim` (TMSL/JSON). It's paired with\n"
        f"a blank `{safe_name}.Report` folder and a top-level `{safe_name}.pbip` file\n"
        "(see the project-level README) so the whole thing opens together in Power BI\n"
        "Desktop.\n\n"
        "This folder intentionally does NOT also include a TMDL `definition/` folder --\n"
        "model.bim and a TMDL folder are mutually exclusive representations of the same\n"
        "model, and shipping both caused this project to fail to open. If you want the\n"
        "TMDL (folder-of-text-files) form instead -- e.g. for Fabric git integration --\n"
        "see the app's \"TMDL files\" tab, or fabric_deploy.py for headless deployment.\n\n"
        "Before deploying for real: review the placeholder Power Query (M) source\n"
        "expressions in model.bim's partitions and point them at your real data source,\n"
        "unless you already generated this with Snowflake connection details filled in.\n"
    )
    files[f"{root}/README.md"] = readme.encode("utf-8")

    return files


def build_pbip_manifest_bytes(report_folder_name: str) -> bytes:
    """The top-level ``<project>.pbip`` manifest that lets Power BI
    Desktop's *File > Open > Power BI Project* recognize and load the
    whole project (report + semantic model together) in one step."""
    doc = {
        "version": "1.0",
        "artifacts": [{"report": {"path": report_folder_name}}],
        "settings": {"enableAutoRecovery": True},
    }
    return json.dumps(doc, indent=2).encode("utf-8")


def build_report_project_files(project_name: str, semantic_model_folder_name: str) -> Dict[str, bytes]:
    """The minimal *thick* PBIR ``.Report`` folder for a Power BI Project:
    one blank page, bound (via ``byPath``) to the sibling
    ``.SemanticModel`` folder -- so opening the top-level ``.pbip`` file
    (or this folder's ``definition.pbir`` directly) in Power BI Desktop
    loads both the report and the semantic model together, no third-party
    tool (Tabular Editor, Fabric workspace, ...) required for that first
    open.

    This is deliberately the smallest valid report: one empty page, no
    theme reference (so there's no dependency on a base-theme resource
    file this app would also have to ship correctly), no visuals. It's
    generated without a real Power BI Desktop available in this
    environment to verify against -- if it doesn't open cleanly in yours,
    fall back to the Tabular Editor or Fabric deployment paths documented
    in the README, which don't depend on this Report scaffold at all.
    """
    safe_name = _safe_project_name(project_name)
    root = f"{safe_name}.Report"
    page_id = uuid.uuid4().hex[:20]

    def _j(doc: Dict[str, Any]) -> bytes:
        return json.dumps(doc, indent=2).encode("utf-8")

    platform_doc = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "Report", "displayName": safe_name},
        "config": {"version": "2.0", "logicalId": str(uuid.uuid4())},
    }
    pbir_doc = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0",
        "datasetReference": {"byPath": {"path": f"../{semantic_model_folder_name}"}},
    }
    version_doc = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json",
        "version": "2.0.0",
    }
    report_doc = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.0.0/schema.json",
        "filterConfig": {"filters": []},
        "objects": {},
        "settings": {
            "useStylableVisualContainerHeader": True,
            "useEnhancedTooltips": True,
        },
        "resourcePackages": [],
    }
    pages_doc = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.0.0/schema.json",
        "pageOrder": [page_id],
        "activePageName": page_id,
    }
    page_doc = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.0.0/schema.json",
        "name": page_id,
        "displayName": "Overview",
        "displayOption": "FitToPage",
        "width": 1280,
        "height": 720,
    }

    return {
        f"{root}/.platform": _j(platform_doc),
        f"{root}/definition.pbir": _j(pbir_doc),
        f"{root}/definition/version.json": _j(version_doc),
        f"{root}/definition/report.json": _j(report_doc),
        f"{root}/definition/pages/pages.json": _j(pages_doc),
        f"{root}/definition/pages/{page_id}/page.json": _j(page_doc),
    }


def build_pbip_zip_bytes(
    ossie_model: Dict[str, Any], project_name: str, data_source: Optional[Dict[str, Any]] = None
) -> bytes:
    """The full Power BI Project ``.zip``: a top-level ``<name>.pbip``
    manifest, a ``<name>.Report`` folder (a minimal blank report bound to
    the model), and the ``<name>.SemanticModel`` folder itself. Unzip and
    open the ``.pbip`` file (or ``.Report/definition.pbir`` directly) in
    Power BI Desktop -- no Tabular Editor, Fabric workspace, or other tool
    needed just to get it open.

    The blank report is a best-effort scaffold (see
    ``build_report_project_files``) generated without a real Power BI
    Desktop to verify against; the bundled top-level README documents the
    Tabular Editor / Fabric fallbacks in case it doesn't open cleanly.
    """
    safe_name = _safe_project_name(project_name)
    sm_folder = f"{safe_name}.SemanticModel"
    report_folder = f"{safe_name}.Report"

    files = build_semantic_model_project_files(ossie_model, project_name, data_source=data_source)
    files.update(build_report_project_files(project_name, sm_folder))
    files[f"{safe_name}.pbip"] = build_pbip_manifest_bytes(report_folder)

    project_readme = (
        f"# {safe_name} -- Power BI Project (generated from Apache Ossie)\n\n"
        f"Unzip this, then open `{safe_name}.pbip` in Power BI Desktop (or open\n"
        f"`{report_folder}/definition.pbir` directly) -- File > Open > Power BI Project,\n"
        "or just double-click the .pbip file if your Desktop version associates it.\n"
        "That loads the (blank) report together with the semantic model in one step.\n"
        "No Tabular Editor, Fabric workspace, or other third-party tool is required\n"
        "just to get this open.\n\n"
        "Two things to know:\n\n"
        f"1. `{report_folder}` is a minimal, blank single-page report scaffold --\n"
        "   generated without a real Power BI Desktop available in this environment to\n"
        "   verify against. If for any reason it doesn't open cleanly in yours, the\n"
        f"   `{sm_folder}` folder still works on its own via Tabular Editor or the\n"
        "   Fabric REST API / git integration -- see that folder's own README.md for\n"
        "   those steps, which don't depend on this Report scaffold at all.\n"
        "2. Before connecting to real data: review the placeholder Power Query (M)\n"
        f"   source expressions in `{sm_folder}/model.bim`'s partitions and point them\n"
        "   at your real data source, unless you already generated this with Snowflake\n"
        "   connection details filled in.\n"
    )
    files[f"{safe_name}_README.md"] = project_readme.encode("utf-8")

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
    pbism_bytes: bytes
    platform_bytes: bytes
    display_name: str


def convert_to_powerbi(
    ossie_model: Dict[str, Any], project_name: str, data_source: Optional[Dict[str, Any]] = None
) -> PowerBiExport:
    tmsl = build_tmsl_model(ossie_model, data_source=data_source)
    safe_name = _safe_project_name(project_name)
    return PowerBiExport(
        tmsl=tmsl,
        tmsl_json=tmsl_to_json_str(tmsl),
        tmdl_files=build_tmdl_files(ossie_model, data_source=data_source),
        zip_bytes=build_pbip_zip_bytes(ossie_model, project_name, data_source=data_source),
        pbism_bytes=build_pbism_bytes(),
        platform_bytes=build_platform_bytes(safe_name),
        display_name=safe_name,
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
