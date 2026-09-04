"""Core parsing and generation logic for turning spreadsheet metadata into
Apache Ossie (Open Semantic Interchange) semantic model YAML documents.

This module is intentionally free of any Streamlit imports so it can be
unit tested and reused from a CLI if needed. See ``app.py`` for the
Streamlit UI that drives this module.

Spec reference: https://github.com/apache/ossie/tree/main/core-spec
"""

from __future__ import annotations

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

_SNAPSHOT_ALIASES = {
    "table_name": "table_name",
    "name": "table_name",
    "snapshot_type": "snapshot_type",
    "snapshot_frequency": "snapshot_frequency",
    "snapshot_date_column": "snapshot_date_column",
    "partition_column": "partition_column",
    "history_type": "history_type",
    "retention_period": "retention_period",
    "source_system": "source_system",
    "load_pattern": "load_pattern",
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
    columns: List[ColumnMeta] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parsing: metadata file
# ---------------------------------------------------------------------------

def parse_metadata(df: pd.DataFrame) -> "Dict[str, TableMeta]":
    norm = normalize_columns(df, _METADATA_ALIASES)
    if "table_name" not in norm.columns or "column_name" not in norm.columns:
        raise ValueError(
            "Metadata file must contain at least a 'Name' (table) and "
            "'Column Title' (column) column."
        )

    tables: "Dict[str, TableMeta]" = {}
    for _, row in norm.iterrows():
        table_name = clean_str(row.get("table_name"))
        column_name = clean_str(row.get("column_name"))
        if not table_name or not column_name:
            continue
        table = tables.setdefault(table_name, TableMeta(table_name=table_name))
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
# Parsing: snapshot details file (custom format)
# ---------------------------------------------------------------------------

def parse_snapshot_details(df: Optional[pd.DataFrame]) -> Dict[str, Dict[str, str]]:
    if df is None or df.empty:
        return {}
    norm = normalize_columns(df, _SNAPSHOT_ALIASES)
    result: Dict[str, Dict[str, str]] = {}
    for _, row in norm.iterrows():
        table_name = clean_str(row.get("table_name"))
        if not table_name:
            continue
        info = {
            k: clean_str(row.get(k))
            for k in (
                "snapshot_type",
                "snapshot_frequency",
                "snapshot_date_column",
                "partition_column",
                "history_type",
                "retention_period",
                "source_system",
                "load_pattern",
            )
            if clean_str(row.get(k))
        }
        result[table_name] = info
    return result


# ---------------------------------------------------------------------------
# Parsing: relationships file (custom format)
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
# Parsing: AI context file (custom format)
# ---------------------------------------------------------------------------

@dataclass
class AiContextEntry:
    instructions: str = ""
    synonyms: List[str] = field(default_factory=list)
    examples: List[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.instructions or self.synonyms or self.examples)

    def to_ossie(self) -> Optional[Dict[str, Any]]:
        if self.is_empty():
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
# YAML construction helpers
# ---------------------------------------------------------------------------

def make_expression(expression: str, dialect: str = "ANSI_SQL") -> Dict[str, Any]:
    return {"dialects": [{"dialect": dialect, "expression": expression}]}


def make_custom_extension(vendor_name: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {"vendor_name": vendor_name, "data": json.dumps(data, ensure_ascii=False)}


def build_field(col: ColumnMeta, dialect: str, ai_context: Optional[AiContextEntry]) -> Dict[str, Any]:
    field_dict: Dict[str, Any] = {
        "name": col.column_name,
        "expression": make_expression(col.column_name, dialect),
    }
    datatype = map_technical_datatype(col.technical_data_type)
    if datatype != "Opaque" or col.technical_data_type:
        field_dict["datatype"] = datatype
    if col.description:
        field_dict["description"] = col.description
    if ai_context is not None:
        ctx = ai_context.to_ossie()
        if ctx:
            field_dict["ai_context"] = ctx

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

    field_dict["custom_extensions"] = [make_custom_extension("COMMON", extension_data)]
    return field_dict


def build_dataset(
    table: TableMeta,
    *,
    dialect: str,
    source_prefix: str,
    snapshot_info: Optional[Dict[str, str]],
    dataset_ai_context: Optional[AiContextEntry],
    field_ai_context: Dict[Tuple[str, str], AiContextEntry],
) -> Dict[str, Any]:
    source = f"{source_prefix}.{table.table_name}" if source_prefix else table.table_name

    primary_key = [c.column_name for c in table.columns if c.is_primary_key]

    fields = []
    for col in table.columns:
        ctx = field_ai_context.get((table.table_name, col.column_name))
        fields.append(build_field(col, dialect, ctx))

    pii_columns = [c.column_name for c in table.columns if c.contains_pii]

    dataset: Dict[str, Any] = {"name": table.table_name, "source": source}
    if primary_key:
        dataset["primary_key"] = primary_key

    descriptions = [c for c in table.columns]  # noqa: F841 (kept for readability)
    if dataset_ai_context is not None:
        ctx = dataset_ai_context.to_ossie()
        if ctx:
            dataset["ai_context"] = ctx

    dataset["fields"] = fields

    extensions = []
    common_data: Dict[str, Any] = {}
    if table.asset_type:
        common_data["asset_type"] = table.asset_type
    if pii_columns:
        common_data["pii_columns"] = pii_columns
    if common_data:
        extensions.append(make_custom_extension("COMMON", common_data))
    if snapshot_info:
        extensions.append(make_custom_extension("SNAPSHOT", snapshot_info))
    if extensions:
        dataset["custom_extensions"] = extensions

    return dataset


def build_relationship(rel: Dict[str, Any], dialect: str) -> Dict[str, Any]:
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


@dataclass
class BuildResult:
    model: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)


def build_semantic_model(
    *,
    model_name: str,
    model_description: str,
    dialect: str,
    source_prefix: str,
    tables: Dict[str, TableMeta],
    snapshot_details: Dict[str, Dict[str, str]],
    relationships: List[Dict[str, Any]],
    model_ai_context: Optional[AiContextEntry],
    dataset_ai_context: Dict[str, AiContextEntry],
    field_ai_context: Dict[Tuple[str, str], AiContextEntry],
) -> BuildResult:
    warnings: List[str] = []
    if not tables:
        raise ValueError("No tables/columns found. Check the metadata file contents.")

    datasets = []
    for table_name, table in tables.items():
        dataset = build_dataset(
            table,
            dialect=dialect,
            source_prefix=source_prefix,
            snapshot_info=snapshot_details.get(table_name),
            dataset_ai_context=dataset_ai_context.get(table_name),
            field_ai_context=field_ai_context,
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
        rel_out.append(build_relationship(rel, dialect))

    semantic_model_entry: Dict[str, Any] = {"name": model_name}
    if model_description:
        semantic_model_entry["description"] = model_description
    if model_ai_context is not None:
        ctx = model_ai_context.to_ossie()
        if ctx:
            semantic_model_entry["ai_context"] = ctx
    semantic_model_entry["datasets"] = datasets
    if rel_out:
        semantic_model_entry["relationships"] = rel_out

    model = {"version": OSSIE_VERSION, "semantic_model": [semantic_model_entry]}
    return BuildResult(model=model, warnings=warnings)


# ---------------------------------------------------------------------------
# YAML rendering
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
