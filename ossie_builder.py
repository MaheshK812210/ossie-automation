"""Core parsing and generation logic for turning spreadsheet metadata into
Apache Ossie (Open Semantic Interchange) semantic model YAML documents.

This module is intentionally free of any Streamlit imports so it can be
unit tested and reused from a CLI if needed. See ``app.py`` for the
Streamlit UI that drives this module.

Spec reference: https://github.com/apache/ossie/tree/main/core-spec

Two-stage workflow
-------------------
1. **Base generation**: ``parse_metadata`` (table/column metadata, required)
   + ``parse_metrics_synonyms_extensions`` (metrics, field synonyms, and
   custom_extensions placeholders, optional) + ``parse_relationships``
   (optional) are combined with ``build_semantic_model`` into a base Ossie
   YAML document. The caller (``app.py``) lets the user view/edit that YAML
   directly before moving on.
2. **AI-context enrichment** (optional, later): ``parse_ai_context`` is
   parsed from a separate file and merged into an *existing* model with
   ``merge_ai_context_into_model``, which concatenates additional
   instructions/synonyms/examples onto whatever is already there (never
   overwrites) and appends any ``Custom Extension`` values as new
   ``custom_extensions`` entries.
"""

from __future__ import annotations

import copy
import csv
import io
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yaml

OSSIE_VERSION = "0.2.0.dev0"
VALID_DIALECTS = [
    "ANSI_SQL",
    "SNOWFLAKE",
    "MDX",
    "TABLEAU",
    "DATABRICKS",
    "MAQL",
    "BIGQUERY",
    "THOUGHTSPOT",
]
VALID_DATATYPES = [
    "String",
    "Integer",
    "Decimal",
    "Float",
    "Boolean",
    "Date",
    "Time",
    "DateTime",
    "DateTimeTz",
    "Opaque",
]

MODEL_LEVEL_KEYWORD = "MODEL"  # sentinel "table name" for model-level AI context rows

# ---------------------------------------------------------------------------
# Column normalization helpers
# ---------------------------------------------------------------------------

_METADATA_ALIASES = {
    "name": "table_name",
    "table_name": "table_name",
    "table": "table_name",
    "asset_type": "asset_type",
    "assest_type": "asset_type",  # tolerate the common typo "Assest Type"
    "column_title": "column_name",
    "column_name": "column_name",
    "description": "description",
    "description_from_source_system": "source_description",
    "size": "size",
    "technical_data_type": "technical_data_type",
    "column_position": "column_position",
    "is_primary_key": "is_primary_key",
    "is_nullable": "is_nullable",
    "contains_pii": "contains_pii",
    "primary_key": "primary_key_name",
}

_RELATIONSHIP_ALIASES = {
    "relationship_name": "name",
    "name": "name",
    "from_table": "from_table",
    "from": "from_table",
    "from_columns": "from_columns",
    "from_column": "from_columns",
    "to_table": "to_table",
    "to": "to_table",
    "to_columns": "to_columns",
    "to_column": "to_columns",
    "relationship_type": "relationship_type",
    "description": "description",
}

_AI_CONTEXT_ALIASES = {
    "table_name": "table_name",
    "name": "table_name",
    "column_name": "column_name",
    "column_title": "column_name",
    "instructions": "instructions",
    "synonyms": "synonyms",
    "examples": "examples",
    "custom_extension": "custom_extension",
    "custom_extensions": "custom_extension",
}

# File 2: metrics, field synonyms, and custom_extensions placeholders, all
# in one sheet, discriminated by a "Type" column (Metric / Synonym /
# Custom Extension). This feeds the *base* YAML generation.
_ENRICHMENT_ALIASES = {
    "type": "row_type",
    "row_type": "row_type",
    "table_name": "table_name",
    "name": "table_name",
    "column_name": "column_name",
    "column_title": "column_name",
    "metric_name": "metric_name",
    "metric_expression": "metric_expression",
    "expression": "metric_expression",
    "metric_description": "metric_description",
    "metric_data_type": "metric_datatype",
    "metric_datatype": "metric_datatype",
    "dialect": "metric_dialect",
    "metric_dialect": "metric_dialect",
    "synonyms": "synonyms",
    "custom_extension_vendor": "custom_extension_vendor",
    "custom_extension_data": "custom_extension_data",
}


def normalize_key(raw: str) -> str:
    """Lowercase, trim, and snake_case an arbitrary spreadsheet header."""
    if raw is None:
        return ""
    s = str(raw).strip().lower()
    s = re.sub(r"[^0-9a-z]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def normalize_columns(df: pd.DataFrame, aliases: Dict[str, str]) -> pd.DataFrame:
    """Rename a dataframe's columns using ``aliases`` after normalizing headers.

    Unrecognized columns are dropped so callers only need to reason about the
    canonical field names. Duplicate canonical names (e.g. an aliased and a
    literal header both mapping to the same field) keep the first occurrence.
    """
    rename_map = {}
    for col in df.columns:
        key = normalize_key(col)
        if key in aliases:
            rename_map[col] = aliases[key]
    renamed = df.rename(columns=rename_map)
    keep = [c for c in renamed.columns if c in set(aliases.values())]
    out = renamed[keep].copy()
    # If aliasing produced duplicate columns, keep the first non-null per row.
    out = out.loc[:, ~out.columns.duplicated()]
    return out


# ---------------------------------------------------------------------------
# File loading (CSV / XLSX / TXT) -- pure bytes in, DataFrame out
# ---------------------------------------------------------------------------

def _sniff_delimiter(sample: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        return dialect.delimiter
    except csv.Error:
        return ","


def load_tabular_bytes(filename: str, raw: bytes) -> pd.DataFrame:
    """Load a CSV, XLSX, or delimited TXT file (given as raw bytes) into a
    single DataFrame. XLSX workbooks with multiple sheets are concatenated.
    """
    lower = filename.lower()
    if lower.endswith(".xlsx") or lower.endswith(".xls"):
        sheets = pd.read_excel(io.BytesIO(raw), sheet_name=None, dtype=str)
        frames = [df for df in sheets.values() if not df.empty]
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    text = raw.decode("utf-8-sig", errors="replace")
    if lower.endswith(".csv"):
        delimiter = ","
    else:  # .txt or unknown -- sniff it
        sample = "\n".join(text.splitlines()[:5])
        delimiter = _sniff_delimiter(sample) if sample.strip() else ","
    df = pd.read_csv(io.StringIO(text), delimiter=delimiter, dtype=str)
    return df


# ---------------------------------------------------------------------------
# Value coercion helpers
# ---------------------------------------------------------------------------

_TRUTHY = {"y", "yes", "true", "1", "t", "pk", "x"}
_FALSY = {"n", "no", "false", "0", "f", ""}


def to_bool(value: Any, default: Optional[bool] = None) -> Optional[bool]:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and pd.isna(value):
        return default
    s = str(value).strip().lower()
    if s in _TRUTHY:
        return True
    if s in _FALSY:
        return default if s == "" else False
    return default


def clean_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    s = str(value).strip()
    if s.lower() == "nan":
        return ""
    return s


def split_list(value: Any) -> List[str]:
    """Split a comma/pipe/semicolon separated cell into a list of tokens."""
    s = clean_str(value)
    if not s:
        return []
    parts = re.split(r"[,;|]", s)
    return [p.strip() for p in parts if p.strip()]


def to_int(value: Any) -> Optional[int]:
    s = clean_str(value)
    if not s:
        return None
    m = re.search(r"-?\d+", s)
    if not m:
        return None
    try:
        return int(m.group())
    except ValueError:
        return None


def dedupe(items: List[str]) -> List[str]:
    return list(dict.fromkeys(items))


# ---------------------------------------------------------------------------
# Technical data type -> Ossie DataType mapping
# ---------------------------------------------------------------------------

_DATATYPE_RULES: List[Tuple[str, str]] = [
    (r"bool|bit\b", "Boolean"),
    (r"timestamp.*(tz|zone|with time zone)|datetimeoffset|zoned", "DateTimeTz"),
    (r"timestamp|datetime|datetime2", "DateTime"),
    (r"^time\b|time without", "Time"),
    (r"^date\b|^d_date$", "Date"),
    (r"decimal|numeric|number\(|money|currency", "Decimal"),
    (r"float|double|real", "Float"),
    (r"int|serial|smallint|bigint|tinyint", "Integer"),
    (r"char|text|clob|string|varchar|nchar|uuid|guid", "String"),
]


def map_technical_datatype(raw: Any) -> str:
    """Best-effort mapping from a free-form technical/physical data type
    (e.g. ``VARCHAR2(200)``, ``NUMBER(18,2)``, ``TIMESTAMP``) to one of the
    Ossie core ``DataType`` enum values. Falls back to ``Opaque`` when the
    type can't be confidently classified, per the spec's guidance to omit
    or use ``Opaque`` for unknown types.
    """
    s = clean_str(raw).lower()
    if not s:
        return "Opaque"
    for pattern, datatype in _DATATYPE_RULES:
        if re.search(pattern, s):
            return datatype
    return "Opaque"


# ---------------------------------------------------------------------------
# Data classes for parsed intermediate structures
# ---------------------------------------------------------------------------

@dataclass
class ColumnMeta:
    table_name: str
    column_name: str
    description: str = ""
    source_description: str = ""
    size: str = ""
    technical_data_type: str = ""
    column_position: Optional[int] = None
    is_primary_key: bool = False
    is_nullable: bool = True
    contains_pii: bool = False
    primary_key_name: str = ""


@dataclass
class TableMeta:
    table_name: str
    asset_type: str = ""
    source: str = ""
    columns: List[ColumnMeta] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parsing: metadata file (File 1, required)
# ---------------------------------------------------------------------------

def parse_qualified_table_name(raw: str) -> Tuple[str, str]:
    """Splits the metadata file's ``Name`` column into ``(short_table_name,
    source)``. There's no separate "database.schema" setting anywhere else
    in the app -- the source is inferred entirely from this one column.

    - ``"FACT_POSITION"`` -> short name ``FACT_POSITION``, source
      ``FACT_POSITION`` (no qualification given).
    - ``"WEALTH_DB.PUBLIC.FACT_POSITION"`` -> short name ``FACT_POSITION``
      (used as the Ossie dataset name and in relationship/metric
      references), source ``WEALTH_DB.PUBLIC.FACT_POSITION`` (used
      verbatim as the dataset's ``source`` field).
    """
    raw = raw.strip()
    if "." in raw:
        return raw.rsplit(".", 1)[-1], raw
    return raw, raw


def parse_metadata(df: pd.DataFrame) -> "Dict[str, TableMeta]":
    norm = normalize_columns(df, _METADATA_ALIASES)
    if "table_name" not in norm.columns or "column_name" not in norm.columns:
        raise ValueError(
            "Metadata file must contain at least a 'Name' (table) and "
            "'Column Title' (column) column."
        )

    tables: "Dict[str, TableMeta]" = {}
    for _, row in norm.iterrows():
        raw_name = clean_str(row.get("table_name"))
        column_name = clean_str(row.get("column_name"))
        if not raw_name or not column_name:
            continue
        table_name, source = parse_qualified_table_name(raw_name)
        table = tables.setdefault(table_name, TableMeta(table_name=table_name, source=source))
        if not table.source:
            table.source = source
        asset_type = clean_str(row.get("asset_type"))
        if asset_type and not table.asset_type:
            table.asset_type = asset_type

        col = ColumnMeta(
            table_name=table_name,
            column_name=column_name,
            description=clean_str(row.get("description")),
            source_description=clean_str(row.get("source_description")),
            size=clean_str(row.get("size")),
            technical_data_type=clean_str(row.get("technical_data_type")),
            column_position=to_int(row.get("column_position")),
            is_primary_key=bool(to_bool(row.get("is_primary_key"), default=False)),
            is_nullable=bool(to_bool(row.get("is_nullable"), default=True)),
            contains_pii=bool(to_bool(row.get("contains_pii"), default=False)),
            primary_key_name=clean_str(row.get("primary_key_name")),
        )
        table.columns.append(col)

    # Fill missing column positions using file order, then sort.
    for table in tables.values():
        for idx, col in enumerate(table.columns, start=1):
            if col.column_position is None:
                col.column_position = idx
        table.columns.sort(key=lambda c: (c.column_position or 0))

    return tables


# ---------------------------------------------------------------------------
# Parsing: metrics, field synonyms & custom_extensions placeholders
# (File 2, optional -- feeds the *base* YAML generation alongside File 1)
# ---------------------------------------------------------------------------

@dataclass
class EnrichmentResult:
    metrics: List[Dict[str, str]] = field(default_factory=list)
    field_synonyms: Dict[Tuple[str, str], List[str]] = field(default_factory=dict)
    dataset_extensions: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    field_extensions: Dict[Tuple[str, str], List[Dict[str, Any]]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def _coerce_custom_extension_data(raw_text: str) -> Any:
    """Custom-extension "data" cells may contain a JSON object/array (used
    verbatim) or free text (wrapped as ``{"note": "..."}``) so any plain
    string a business user types in a spreadsheet still produces valid
    Ossie ``custom_extensions[].data`` (a JSON string).
    """
    try:
        return json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return {"note": raw_text}


def parse_metrics_synonyms_extensions(df: Optional[pd.DataFrame]) -> EnrichmentResult:
    result = EnrichmentResult()
    if df is None or df.empty:
        return result

    norm = normalize_columns(df, _ENRICHMENT_ALIASES)
    for pos, (_, row) in enumerate(norm.iterrows()):
        line_no = pos + 2  # +1 for 0-index, +1 for the header row
        row_type = clean_str(row.get("row_type")).upper().replace(" ", "_")
        table_name = clean_str(row.get("table_name"))
        column_name = clean_str(row.get("column_name"))

        has_any_data = any(
            clean_str(row.get(k))
            for k in (
                "table_name",
                "column_name",
                "metric_name",
                "metric_expression",
                "synonyms",
                "custom_extension_vendor",
                "custom_extension_data",
            )
        )
        if not row_type:
            if has_any_data:
                result.warnings.append(
                    f"Row {line_no}: missing 'Type' (Metric/Synonym/Custom "
                    f"Extension) -- skipped."
                )
            continue

        if row_type == "METRIC":
            name = clean_str(row.get("metric_name"))
            expr = clean_str(row.get("metric_expression"))
            if not name or not expr:
                result.warnings.append(
                    f"Row {line_no}: METRIC row missing 'Metric Name' or "
                    f"'Metric Expression' -- skipped."
                )
                continue
            dialect = clean_str(row.get("metric_dialect")).upper() or "ANSI_SQL"
            if dialect not in VALID_DIALECTS:
                result.warnings.append(
                    f"Row {line_no}: unknown Dialect '{dialect}' for metric "
                    f"'{name}' -- falling back to ANSI_SQL."
                )
                dialect = "ANSI_SQL"
            result.metrics.append(
                {
                    "name": name,
                    "expression": expr,
                    "description": clean_str(row.get("metric_description")),
                    "datatype": clean_str(row.get("metric_datatype")),
                    "dialect": dialect,
                }
            )
        elif row_type in ("SYNONYM", "FIELD_SYNONYM"):
            syns = split_list(row.get("synonyms"))
            if not table_name or not column_name or not syns:
                result.warnings.append(
                    f"Row {line_no}: SYNONYM row missing 'Table Name', "
                    f"'Column Name', or 'Synonyms' -- skipped."
                )
                continue
            key = (table_name, column_name)
            result.field_synonyms.setdefault(key, []).extend(syns)
        elif row_type in ("CUSTOM_EXTENSION", "EXTENSION"):
            vendor = clean_str(row.get("custom_extension_vendor")) or "COMMON"
            data_raw = clean_str(row.get("custom_extension_data"))
            if not table_name or not data_raw:
                result.warnings.append(
                    f"Row {line_no}: CUSTOM EXTENSION row missing 'Table "
                    f"Name' or 'Custom Extension Data' -- skipped."
                )
                continue
            ext = {
                "vendor_name": vendor,
                "data": json.dumps(_coerce_custom_extension_data(data_raw), ensure_ascii=False),
            }
            if column_name:
                result.field_extensions.setdefault((table_name, column_name), []).append(ext)
            else:
                result.dataset_extensions.setdefault(table_name, []).append(ext)
        else:
            result.warnings.append(
                f"Row {line_no}: unknown Type '{row_type}' -- expected "
                f"Metric, Synonym, or Custom Extension -- skipped."
            )

    # Deduplicate accumulated synonym lists while preserving order.
    result.field_synonyms = {k: dedupe(v) for k, v in result.field_synonyms.items()}
    return result


# ---------------------------------------------------------------------------
# Parsing: relationships file (File 3, optional, custom format)
# ---------------------------------------------------------------------------

def parse_relationships(df: Optional[pd.DataFrame]) -> List[Dict[str, Any]]:
    if df is None or df.empty:
        return []
    norm = normalize_columns(df, _RELATIONSHIP_ALIASES)
    relationships = []
    for _, row in norm.iterrows():
        from_table = clean_str(row.get("from_table"))
        to_table = clean_str(row.get("to_table"))
        from_cols = split_list(row.get("from_columns"))
        to_cols = split_list(row.get("to_columns"))
        if not from_table or not to_table or not from_cols or not to_cols:
            continue
        name = clean_str(row.get("name")) or f"{from_table}_to_{to_table}"
        relationships.append(
            {
                "name": name,
                "from_table": from_table,
                "to_table": to_table,
                "from_columns": from_cols,
                "to_columns": to_cols,
                "relationship_type": clean_str(row.get("relationship_type")),
                "description": clean_str(row.get("description")),
            }
        )
    return relationships


# ---------------------------------------------------------------------------
# Parsing: AI context enrichment file (File 4, optional, custom format)
#
# Uploaded *after* a base YAML already exists. Merged in via
# ``merge_ai_context_into_model`` below, which concatenates onto whatever
# is already present rather than overwriting it.
# ---------------------------------------------------------------------------

@dataclass
class AiContextEntry:
    instructions: str = ""
    synonyms: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)
    custom_extension: str = ""

    def is_empty(self) -> bool:
        return not (self.instructions or self.synonyms or self.examples or self.custom_extension)

    def to_ossie(self) -> Optional[Dict[str, Any]]:
        if not (self.instructions or self.synonyms or self.examples):
            return None
        out: Dict[str, Any] = {}
        if self.instructions:
            out["instructions"] = self.instructions
        if self.synonyms:
            out["synonyms"] = self.synonyms
        if self.examples:
            out["examples"] = self.examples
        return out


def parse_ai_context(
    df: Optional[pd.DataFrame],
) -> Tuple[Optional[AiContextEntry], Dict[str, AiContextEntry], Dict[Tuple[str, str], AiContextEntry]]:
    """Returns (model_context, {table_name: context}, {(table, column): context})."""
    model_ctx: Optional[AiContextEntry] = None
    dataset_ctx: Dict[str, AiContextEntry] = {}
    field_ctx: Dict[Tuple[str, str], AiContextEntry] = {}

    if df is None or df.empty:
        return model_ctx, dataset_ctx, field_ctx

    norm = normalize_columns(df, _AI_CONTEXT_ALIASES)
    for _, row in norm.iterrows():
        table_name = clean_str(row.get("table_name"))
        column_name = clean_str(row.get("column_name"))
        entry = AiContextEntry(
            instructions=clean_str(row.get("instructions")),
            synonyms=split_list(row.get("synonyms")),
            examples=split_list(row.get("examples")),
            custom_extension=clean_str(row.get("custom_extension")),
        )
        if entry.is_empty():
            continue
        if not table_name or table_name.upper() == MODEL_LEVEL_KEYWORD:
            model_ctx = entry
        elif not column_name:
            dataset_ctx[table_name] = entry
        else:
            field_ctx[(table_name, column_name)] = entry

    return model_ctx, dataset_ctx, field_ctx


# ---------------------------------------------------------------------------
# YAML construction helpers -- base generation
# ---------------------------------------------------------------------------

# Simple column-reference field expressions are dialect-agnostic (just the
# bare column name), so fields always use ANSI_SQL. Only metrics (whose
# expressions can contain dialect-specific aggregate SQL) carry their own
# per-row dialect, set via the "Dialect" column in the metrics file.
FIELD_DIALECT = "ANSI_SQL"


def make_expression(expression: str, dialect: str = "ANSI_SQL") -> Dict[str, Any]:
    return {"dialects": [{"dialect": dialect, "expression": expression}]}


def make_custom_extension(vendor_name: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {"vendor_name": vendor_name, "data": json.dumps(data, ensure_ascii=False)}


def build_field(
    col: ColumnMeta,
    *,
    extra_synonyms: Optional[List[str]] = None,
    extra_custom_extensions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    field_dict: Dict[str, Any] = {
        "name": col.column_name,
        "expression": make_expression(col.column_name, FIELD_DIALECT),
    }
    datatype = map_technical_datatype(col.technical_data_type)
    if datatype != "Opaque" or col.technical_data_type:
        field_dict["datatype"] = datatype
    if col.description:
        field_dict["description"] = col.description

    if extra_synonyms:
        field_dict["ai_context"] = {"synonyms": dedupe(extra_synonyms)}

    extension_data: Dict[str, Any] = {}
    if col.technical_data_type:
        extension_data["technical_data_type"] = col.technical_data_type
    if col.size:
        extension_data["size"] = col.size
    extension_data["column_position"] = col.column_position
    extension_data["is_nullable"] = col.is_nullable
    extension_data["contains_pii"] = col.contains_pii
    if col.source_description:
        extension_data["description_from_source_system"] = col.source_description
    if col.primary_key_name:
        extension_data["primary_key_name"] = col.primary_key_name

    extensions = [make_custom_extension("COMMON", extension_data)]
    if extra_custom_extensions:
        extensions.extend(extra_custom_extensions)
    field_dict["custom_extensions"] = extensions
    return field_dict


def build_dataset(
    table: TableMeta,
    *,
    field_synonyms: Dict[Tuple[str, str], List[str]],
    field_extensions: Dict[Tuple[str, str], List[Dict[str, Any]]],
    dataset_extensions: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    source = table.source or table.table_name

    primary_key = [c.column_name for c in table.columns if c.is_primary_key]

    fields = []
    for col in table.columns:
        key = (table.table_name, col.column_name)
        fields.append(
            build_field(
                col,
                extra_synonyms=field_synonyms.get(key),
                extra_custom_extensions=field_extensions.get(key),
            )
        )

    pii_columns = [c.column_name for c in table.columns if c.contains_pii]

    dataset: Dict[str, Any] = {"name": table.table_name, "source": source}
    if primary_key:
        dataset["primary_key"] = primary_key

    dataset["fields"] = fields

    extensions = []
    common_data: Dict[str, Any] = {}
    if table.asset_type:
        common_data["asset_type"] = table.asset_type
    if pii_columns:
        common_data["pii_columns"] = pii_columns
    if common_data:
        extensions.append(make_custom_extension("COMMON", common_data))
    extensions.extend(dataset_extensions.get(table.table_name, []))
    if extensions:
        dataset["custom_extensions"] = extensions

    return dataset


def build_relationship(rel: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "name": rel["name"],
        "from": rel["from_table"],
        "to": rel["to_table"],
        "from_columns": rel["from_columns"],
        "to_columns": rel["to_columns"],
    }
    ai_ctx: Dict[str, Any] = {}
    if rel.get("description"):
        ai_ctx["instructions"] = rel["description"]
    if rel.get("relationship_type"):
        ai_ctx.setdefault("synonyms", []).append(rel["relationship_type"])
    if ai_ctx:
        out["ai_context"] = ai_ctx
    if rel.get("relationship_type"):
        out["custom_extensions"] = [
            make_custom_extension("COMMON", {"relationship_type": rel["relationship_type"]})
        ]
    return out


def build_metric(metric: Dict[str, str]) -> Dict[str, Any]:
    dialect = metric.get("dialect") or "ANSI_SQL"
    out: Dict[str, Any] = {
        "name": metric["name"],
        "expression": make_expression(metric["expression"], dialect),
    }
    if metric.get("description"):
        out["description"] = metric["description"]
    raw_dt = clean_str(metric.get("datatype"))
    if raw_dt:
        out["datatype"] = raw_dt if raw_dt in VALID_DATATYPES else map_technical_datatype(raw_dt)
    return out


@dataclass
class BuildResult:
    model: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)


def build_semantic_model(
    *,
    model_name: str,
    model_description: str,
    tables: Dict[str, TableMeta],
    relationships: List[Dict[str, Any]],
    metrics: Optional[List[Dict[str, str]]] = None,
    field_synonyms: Optional[Dict[Tuple[str, str], List[str]]] = None,
    dataset_extensions: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    field_extensions: Optional[Dict[Tuple[str, str], List[Dict[str, Any]]]] = None,
) -> BuildResult:
    """Build the *base* Ossie semantic model from table/column metadata plus
    optional metrics, field synonyms, custom_extensions placeholders, and
    relationships. AI-context enrichment is intentionally NOT handled here
    -- see ``merge_ai_context_into_model`` for the later enrichment step.

    There is no global dialect or source-prefix setting: each dataset's
    ``source`` comes from the metadata file's ``Name`` column (see
    ``parse_qualified_table_name``), and each metric carries its own
    dialect from the metrics file's ``Dialect`` column (defaulting to
    ANSI_SQL). Field expressions are always ANSI_SQL (see ``FIELD_DIALECT``).
    """
    metrics = metrics or []
    field_synonyms = field_synonyms or {}
    dataset_extensions = dataset_extensions or {}
    field_extensions = field_extensions or {}

    warnings: List[str] = []
    if not tables:
        raise ValueError("No tables/columns found. Check the metadata file contents.")

    datasets = []
    for table_name, table in tables.items():
        dataset = build_dataset(
            table,
            field_synonyms=field_synonyms,
            field_extensions=field_extensions,
            dataset_extensions=dataset_extensions,
        )
        datasets.append(dataset)

    known_tables = set(tables.keys())
    rel_out = []
    for rel in relationships:
        if rel["from_table"] not in known_tables:
            warnings.append(
                f"Relationship '{rel['name']}' references unknown table "
                f"'{rel['from_table']}' (from) -- skipped."
            )
            continue
        if rel["to_table"] not in known_tables:
            warnings.append(
                f"Relationship '{rel['name']}' references unknown table "
                f"'{rel['to_table']}' (to) -- skipped."
            )
            continue
        rel_out.append(build_relationship(rel))

    semantic_model_entry: Dict[str, Any] = {"name": model_name}
    if model_description:
        semantic_model_entry["description"] = model_description
    semantic_model_entry["datasets"] = datasets
    if rel_out:
        semantic_model_entry["relationships"] = rel_out

    metrics_out = []
    for m in metrics:
        try:
            metrics_out.append(build_metric(m))
        except KeyError as exc:  # pragma: no cover -- defensive
            warnings.append(f"Skipping malformed metric entry: missing {exc}.")
    if metrics_out:
        semantic_model_entry["metrics"] = metrics_out

    model = {"version": OSSIE_VERSION, "semantic_model": [semantic_model_entry]}
    return BuildResult(model=model, warnings=warnings)


# ---------------------------------------------------------------------------
# AI-context enrichment -- merges into an EXISTING (possibly user-edited)
# model, concatenating rather than overwriting.
# ---------------------------------------------------------------------------

def _merge_ai_context_block(
    existing: Optional[Dict[str, Any]], entry: AiContextEntry
) -> Optional[Dict[str, Any]]:
    merged = dict(existing) if existing else {}
    if entry.instructions:
        if merged.get("instructions"):
            merged["instructions"] = f"{merged['instructions'].rstrip()}\n\n{entry.instructions}"
        else:
            merged["instructions"] = entry.instructions
    if entry.synonyms:
        merged["synonyms"] = dedupe(list(merged.get("synonyms") or []) + entry.synonyms)
    if entry.examples:
        merged["examples"] = dedupe(list(merged.get("examples") or []) + entry.examples)
    return merged or None


def _append_custom_extension_from_text(
    existing: Optional[List[Dict[str, Any]]], vendor_name: str, raw_text: str
) -> List[Dict[str, Any]]:
    lst = list(existing) if existing else []
    if not raw_text:
        return lst
    lst.append(
        {
            "vendor_name": vendor_name,
            "data": json.dumps(_coerce_custom_extension_data(raw_text), ensure_ascii=False),
        }
    )
    return lst


def merge_ai_context_into_model(
    model: Dict[str, Any],
    model_ctx: Optional[AiContextEntry],
    dataset_ctx: Dict[str, AiContextEntry],
    field_ctx: Dict[Tuple[str, str], AiContextEntry],
) -> Dict[str, Any]:
    """Return a NEW model dict with AI-context instructions/synonyms/examples
    concatenated onto whatever is already present (never overwritten), and
    any ``Custom Extension`` cell values appended as new ``custom_extensions``
    entries (``vendor_name: AI_ENRICHMENT``). Safe to call repeatedly --
    each call only adds to the model, so re-running enrichment (e.g. after
    manual edits) is idempotent-ish additive rather than destructive.
    """
    new_model = copy.deepcopy(model)
    entries = new_model.get("semantic_model") or []
    if not entries:
        return new_model
    sm = entries[0]

    if model_ctx is not None:
        merged = _merge_ai_context_block(sm.get("ai_context"), model_ctx)
        if merged:
            sm["ai_context"] = merged
        if model_ctx.custom_extension:
            sm["custom_extensions"] = _append_custom_extension_from_text(
                sm.get("custom_extensions"), "AI_ENRICHMENT", model_ctx.custom_extension
            )

    for dataset in sm.get("datasets", []):
        ctx = dataset_ctx.get(dataset.get("name"))
        if ctx is not None:
            merged = _merge_ai_context_block(dataset.get("ai_context"), ctx)
            if merged:
                dataset["ai_context"] = merged
            if ctx.custom_extension:
                dataset["custom_extensions"] = _append_custom_extension_from_text(
                    dataset.get("custom_extensions"), "AI_ENRICHMENT", ctx.custom_extension
                )
        for fld in dataset.get("fields", []):
            fctx = field_ctx.get((dataset.get("name"), fld.get("name")))
            if fctx is not None:
                merged = _merge_ai_context_block(fld.get("ai_context"), fctx)
                if merged:
                    fld["ai_context"] = merged
                if fctx.custom_extension:
                    fld["custom_extensions"] = _append_custom_extension_from_text(
                        fld.get("custom_extensions"), "AI_ENRICHMENT", fctx.custom_extension
                    )

    return new_model


# ---------------------------------------------------------------------------
# YAML rendering / parsing
# ---------------------------------------------------------------------------

class _OrderedDumper(yaml.SafeDumper):
    pass


def _str_presenter(dumper: yaml.Dumper, data: str):
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_OrderedDumper.add_representer(str, _str_presenter)


def to_yaml(model: Dict[str, Any]) -> str:
    header = (
        "# Generated by the Streamlit \"Ossie Semantic Model Builder\"\n"
        "# Spec: https://github.com/apache/ossie/tree/main/core-spec\n"
        f"# yaml-language-server: $schema=./ossie-schema.json\n"
    )
    body = yaml.dump(
        model,
        Dumper=_OrderedDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=100,
    )
    return header + body


def parse_yaml_text(text: str) -> Dict[str, Any]:
    """Parse user-edited YAML text back into a model dict. Raises
    ``yaml.YAMLError`` on malformed YAML."""
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ValueError("The YAML document must parse to a mapping (object) at the top level.")
    return loaded


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

def validate_model(model: Dict[str, Any], schema_path: str) -> List[str]:
    """Validate ``model`` against the bundled Ossie JSON Schema.

    Returns a list of human-readable error strings (empty if valid).
    """
    import jsonschema

    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    validator = jsonschema.Draft202012Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(model), key=lambda e: list(e.path)):
        path = "/".join(str(p) for p in err.path) or "<root>"
        errors.append(f"{path}: {err.message}")
    return errors
