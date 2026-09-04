import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ossie_builder as ob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_DIR = os.path.join(ROOT, "sample_data")
SCHEMA_PATH = os.path.join(ROOT, "schema", "ossie-schema.json")


def _load_sample(name):
    path = os.path.join(SAMPLE_DIR, name)
    with open(path, "rb") as f:
        raw = f.read()
    return ob.load_tabular_bytes(name, raw)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("VARCHAR2(200)", "String"),
        ("NUMBER(18,2)", "Decimal"),
        ("NUMBER(10)", "Decimal"),
        ("INT", "Integer"),
        ("BIGINT", "Integer"),
        ("DATE", "Date"),
        ("TIMESTAMP", "DateTime"),
        ("TIMESTAMP WITH TIME ZONE", "DateTimeTz"),
        ("BOOLEAN", "Boolean"),
        ("CHAR(1)", "String"),
        ("FLOAT", "Float"),
        ("", "Opaque"),
        ("SOME_WEIRD_TYPE", "Opaque"),
    ],
)
def test_map_technical_datatype(raw, expected):
    assert ob.map_technical_datatype(raw) == expected


def test_to_bool():
    assert ob.to_bool("Y") is True
    assert ob.to_bool("n") is False
    assert ob.to_bool("Yes") is True
    assert ob.to_bool("No") is False
    assert ob.to_bool("") is None
    assert ob.to_bool(None, default=True) is True


def test_parse_metadata_groups_columns_by_table():
    df = _load_sample("01_table_column_metadata.csv")
    tables = ob.parse_metadata(df)
    assert "FACT_POSITION" in tables
    assert "DIM_ACCOUNT" in tables
    fact = tables["FACT_POSITION"]
    assert fact.asset_type == "Fact Table"
    pk_cols = [c.column_name for c in fact.columns if c.is_primary_key]
    assert pk_cols == ["ACCOUNT_ID", "SECURITY_ID", "AS_OF_DATE_ID"]


def test_parse_snapshot_details():
    df = _load_sample("02_snapshot_details.csv")
    snaps = ob.parse_snapshot_details(df)
    assert snaps["FACT_POSITION"]["snapshot_frequency"] == "Daily"


def test_parse_relationships():
    df = _load_sample("03_relationships.csv")
    rels = ob.parse_relationships(df)
    names = {r["name"] for r in rels}
    assert "FACT_POSITION_TO_ACCOUNT" in names


def test_parse_ai_context():
    df = _load_sample("04_ai_context.csv")
    model_ctx, dataset_ctx, field_ctx = ob.parse_ai_context(df)
    assert model_ctx is not None
    assert "FACT_POSITION" in dataset_ctx
    assert ("FACT_POSITION", "MARKET_VALUE") in field_ctx


def test_full_pipeline_produces_schema_valid_yaml():
    metadata_df = _load_sample("01_table_column_metadata.csv")
    snapshot_df = _load_sample("02_snapshot_details.csv")
    rel_df = _load_sample("03_relationships.csv")
    ai_df = _load_sample("04_ai_context.csv")

    tables = ob.parse_metadata(metadata_df)
    snapshot_details = ob.parse_snapshot_details(snapshot_df)
    relationships = ob.parse_relationships(rel_df)
    model_ctx, dataset_ctx, field_ctx = ob.parse_ai_context(ai_df)

    result = ob.build_semantic_model(
        model_name="account_position_model",
        model_description="Account and position semantic model",
        dialect="ANSI_SQL",
        source_prefix="wealth.public",
        tables=tables,
        snapshot_details=snapshot_details,
        relationships=relationships,
        model_ai_context=model_ctx,
        dataset_ai_context=dataset_ctx,
        field_ai_context=field_ctx,
    )
    assert result.warnings == []

    errors = ob.validate_model(result.model, SCHEMA_PATH)
    assert errors == [], f"Schema validation errors: {errors}"

    yaml_text = ob.to_yaml(result.model)
    assert "semantic_model" in yaml_text
    assert "FACT_POSITION" in yaml_text

    # Ensure primary_key ordering and composite key survive round trip.
    ds_by_name = {d["name"]: d for d in result.model["semantic_model"][0]["datasets"]}
    assert ds_by_name["FACT_POSITION"]["primary_key"] == ["ACCOUNT_ID", "SECURITY_ID", "AS_OF_DATE_ID"]
    assert ds_by_name["DIM_CLIENT"]["primary_key"] == ["CLIENT_ID"]


def test_unknown_table_in_relationship_generates_warning():
    tables = {
        "A": ob.TableMeta(
            table_name="A",
            columns=[ob.ColumnMeta(table_name="A", column_name="id", is_primary_key=True, column_position=1)],
        )
    }
    relationships = [
        {
            "name": "a_to_missing",
            "from_table": "A",
            "to_table": "DOES_NOT_EXIST",
            "from_columns": ["id"],
            "to_columns": ["id"],
            "relationship_type": "",
            "description": "",
        }
    ]
    result = ob.build_semantic_model(
        model_name="m",
        model_description="",
        dialect="ANSI_SQL",
        source_prefix="",
        tables=tables,
        snapshot_details={},
        relationships=relationships,
        model_ai_context=None,
        dataset_ai_context={},
        field_ai_context={},
    )
    assert len(result.warnings) == 1
    assert "relationships" not in result.model["semantic_model"][0]
