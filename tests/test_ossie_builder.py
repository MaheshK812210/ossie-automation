import json
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


def _build_base_model(**overrides):
    metadata_df = _load_sample("01_table_column_metadata.csv")
    enrichment_df = _load_sample("02_metrics_synonyms_extensions.csv")
    rel_df = _load_sample("03_relationships.csv")

    tables = ob.parse_metadata(metadata_df)
    enrichment = ob.parse_metrics_synonyms_extensions(enrichment_df)
    relationships = ob.parse_relationships(rel_df)

    kwargs = dict(
        model_name="account_position_model",
        model_description="Account and position semantic model",
        tables=tables,
        relationships=relationships,
        metrics=enrichment.metrics,
        field_synonyms=enrichment.field_synonyms,
        dataset_extensions=enrichment.dataset_extensions,
        field_extensions=enrichment.field_extensions,
    )
    kwargs.update(overrides)
    return ob.build_semantic_model(**kwargs), enrichment


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


@pytest.mark.parametrize(
    "raw,expected_short,expected_source",
    [
        ("FACT_POSITION", "FACT_POSITION", "FACT_POSITION"),
        ("PUBLIC.FACT_POSITION", "FACT_POSITION", "PUBLIC.FACT_POSITION"),
        ("WEALTH_DB.PUBLIC.FACT_POSITION", "FACT_POSITION", "WEALTH_DB.PUBLIC.FACT_POSITION"),
        ("  WEALTH_DB.PUBLIC.FACT_POSITION  ", "FACT_POSITION", "WEALTH_DB.PUBLIC.FACT_POSITION"),
    ],
)
def test_parse_qualified_table_name(raw, expected_short, expected_source):
    short, source = ob.parse_qualified_table_name(raw)
    assert short == expected_short
    assert source == expected_source


def test_parse_metadata_groups_columns_by_table():
    df = _load_sample("01_table_column_metadata.csv")
    tables = ob.parse_metadata(df)
    assert "FACT_POSITION" in tables
    assert "DIM_ACCOUNT" in tables
    fact = tables["FACT_POSITION"]
    assert fact.asset_type == "Fact Table"
    # The "database.schema" is inferred entirely from the qualified Name
    # column -- there's no separate source-prefix setting anywhere else.
    assert fact.source == "WEALTH_DB.PUBLIC.FACT_POSITION"
    pk_cols = [c.column_name for c in fact.columns if c.is_primary_key]
    assert pk_cols == ["ACCOUNT_ID", "SECURITY_ID", "AS_OF_DATE_ID"]


def test_parse_metrics_synonyms_extensions():
    df = _load_sample("02_metrics_synonyms_extensions.csv")
    result = ob.parse_metrics_synonyms_extensions(df)
    assert result.warnings == []

    metric_names = {m["name"] for m in result.metrics}
    assert "total_market_value" in metric_names
    assert len(result.metrics) == 5
    # Dialect is per-metric (there's no global dialect setting).
    assert all(m["dialect"] == "ANSI_SQL" for m in result.metrics)

    assert result.field_synonyms[("FACT_POSITION", "MARKET_VALUE")] == [
        "position value",
        "holding value",
        "market val",
    ]

    # Table-level custom extension (no Column Name).
    fact_exts = result.dataset_extensions["FACT_POSITION"]
    assert fact_exts[0]["vendor_name"] == "SNOWFLAKE"
    assert json.loads(fact_exts[0]["data"])["clustering_keys"] == ["AS_OF_DATE_ID", "ACCOUNT_ID"]

    # Field-level custom extension (Column Name present).
    acct_num_exts = result.field_extensions[("DIM_ACCOUNT", "ACCOUNT_NUMBER")]
    assert acct_num_exts[0]["vendor_name"] == "COMMON"
    assert json.loads(acct_num_exts[0]["data"])["masking_policy"] == "MASK_ACCOUNT_NUMBER"


def test_parse_metrics_synonyms_extensions_warns_on_bad_rows():
    df = pd.DataFrame(
        [
            {"Type": "Metric", "Metric Name": "", "Metric Expression": ""},
            {"Type": "Bogus", "Table Name": "X"},
            {"Type": "", "Table Name": "X", "Column Name": "Y"},
        ]
    )
    result = ob.parse_metrics_synonyms_extensions(df)
    assert len(result.warnings) == 3


def test_parse_metrics_synonyms_extensions_empty():
    result = ob.parse_metrics_synonyms_extensions(None)
    assert result.metrics == []
    assert result.field_synonyms == {}
    assert result.warnings == []


def test_metric_dialect_defaults_to_ansi_sql_when_blank():
    df = pd.DataFrame(
        [{"Type": "Metric", "Metric Name": "m1", "Metric Expression": "SUM(x.y)", "Dialect": ""}]
    )
    result = ob.parse_metrics_synonyms_extensions(df)
    assert result.warnings == []
    assert result.metrics[0]["dialect"] == "ANSI_SQL"


def test_metric_dialect_respects_valid_value():
    df = pd.DataFrame(
        [{"Type": "Metric", "Metric Name": "m1", "Metric Expression": "SUM(x.y)", "Dialect": "snowflake"}]
    )
    result = ob.parse_metrics_synonyms_extensions(df)
    assert result.warnings == []
    assert result.metrics[0]["dialect"] == "SNOWFLAKE"


def test_metric_dialect_warns_and_falls_back_on_unknown_value():
    df = pd.DataFrame(
        [{"Type": "Metric", "Metric Name": "m1", "Metric Expression": "SUM(x.y)", "Dialect": "NOT_A_DIALECT"}]
    )
    result = ob.parse_metrics_synonyms_extensions(df)
    assert len(result.warnings) == 1
    assert "NOT_A_DIALECT" in result.warnings[0]
    assert result.metrics[0]["dialect"] == "ANSI_SQL"


def test_parse_relationships():
    df = _load_sample("03_relationships.csv")
    rels = ob.parse_relationships(df)
    names = {r["name"] for r in rels}
    assert "FACT_POSITION_TO_ACCOUNT" in names


def test_parse_ai_context():
    df = _load_sample("04_ai_context.csv")
    model_ctx, dataset_ctx, field_ctx = ob.parse_ai_context(df)
    assert model_ctx is not None
    assert model_ctx.custom_extension  # sample row includes one
    assert "FACT_POSITION" in dataset_ctx
    assert ("FACT_POSITION", "MARKET_VALUE") in field_ctx
    assert field_ctx[("FACT_POSITION", "MARKET_VALUE")].custom_extension


def test_full_base_pipeline_produces_schema_valid_yaml():
    result, enrichment = _build_base_model()
    assert result.warnings == []

    errors = ob.validate_model(result.model, SCHEMA_PATH)
    assert errors == [], f"Schema validation errors: {errors}"

    yaml_text = ob.to_yaml(result.model)
    assert "semantic_model" in yaml_text
    assert "FACT_POSITION" in yaml_text
    assert "metrics" in result.model["semantic_model"][0]

    ds_by_name = {d["name"]: d for d in result.model["semantic_model"][0]["datasets"]}
    # Composite primary key survives round trip.
    assert ds_by_name["FACT_POSITION"]["primary_key"] == ["ACCOUNT_ID", "SECURITY_ID", "AS_OF_DATE_ID"]
    assert ds_by_name["DIM_CLIENT"]["primary_key"] == ["CLIENT_ID"]

    # source is inferred from the metadata file's qualified Name column,
    # not from any separate source-prefix setting.
    assert ds_by_name["FACT_POSITION"]["source"] == "WEALTH_DB.PUBLIC.FACT_POSITION"

    # Metrics carry their own per-row dialect.
    total_mv = next(m for m in result.model["semantic_model"][0]["metrics"] if m["name"] == "total_market_value")
    assert total_mv["expression"]["dialects"][0]["dialect"] == "ANSI_SQL"

    # Field-level synonyms from File 2 land as ai_context on the field.
    mv_field = next(f for f in ds_by_name["FACT_POSITION"]["fields"] if f["name"] == "MARKET_VALUE")
    assert mv_field["ai_context"]["synonyms"] == ["position value", "holding value", "market val"]

    # Table-level custom extension placeholder from File 2 lands on the dataset.
    fact_ext_vendors = {e["vendor_name"] for e in ds_by_name["FACT_POSITION"]["custom_extensions"]}
    assert "SNOWFLAKE" in fact_ext_vendors

    # Field-level custom extension placeholder from File 2 lands on the field.
    acct_num_field = next(
        f for f in ds_by_name["DIM_ACCOUNT"]["fields"] if f["name"] == "ACCOUNT_NUMBER"
    )
    acct_num_vendors = {e["vendor_name"] for e in acct_num_field["custom_extensions"]}
    assert "COMMON" in acct_num_vendors  # from the metadata block AND the placeholder

    # Base generation must NOT have touched anything from the (separate,
    # later) AI-context enrichment file.
    assert "AI_ENRICHMENT" not in fact_ext_vendors


def test_ai_context_enrichment_concatenates_not_overwrites():
    result, _ = _build_base_model()
    ai_df = _load_sample("04_ai_context.csv")
    model_ctx, dataset_ctx, field_ctx = ob.parse_ai_context(ai_df)

    enriched = ob.merge_ai_context_into_model(result.model, model_ctx, dataset_ctx, field_ctx)

    # Enrichment must not mutate the original model in place.
    original_fact = next(
        d for d in result.model["semantic_model"][0]["datasets"] if d["name"] == "FACT_POSITION"
    )
    assert "ai_context" not in original_fact

    enriched_entry = enriched["semantic_model"][0]
    assert enriched_entry["ai_context"]["instructions"].startswith("Use this semantic model")

    enriched_fact = next(d for d in enriched_entry["datasets"] if d["name"] == "FACT_POSITION")
    assert enriched_fact["ai_context"]["instructions"].startswith("Daily snapshot")
    fact_ext_vendors = [e["vendor_name"] for e in enriched_fact["custom_extensions"]]
    assert fact_ext_vendors.count("AI_ENRICHMENT") == 1
    assert "SNOWFLAKE" in fact_ext_vendors  # base extension preserved

    # MARKET_VALUE already had base synonyms from File 2; the AI-context
    # file's additional synonyms must be *appended*, not replace them.
    mv_field = next(f for f in enriched_fact["fields"] if f["name"] == "MARKET_VALUE")
    assert mv_field["ai_context"]["synonyms"] == [
        "position value",
        "holding value",
        "market val",
        "mkt value",
        "MV",
    ]
    mv_ext_vendors = [e["vendor_name"] for e in mv_field["custom_extensions"]]
    assert mv_ext_vendors.count("AI_ENRICHMENT") == 1

    errors = ob.validate_model(enriched, SCHEMA_PATH)
    assert errors == [], f"Schema validation errors after enrichment: {errors}"


def test_ai_context_enrichment_is_additive_across_repeated_calls():
    result, _ = _build_base_model()
    entry = ob.AiContextEntry(instructions="Extra note.", synonyms=["foo"])
    once = ob.merge_ai_context_into_model(result.model, entry, {}, {})
    twice = ob.merge_ai_context_into_model(once, entry, {}, {})
    # Calling merge again keeps adding (by design, this is a simple additive
    # merge -- callers are responsible for not re-uploading the same file
    # if they don't want duplicate concatenation).
    assert twice["semantic_model"][0]["ai_context"]["instructions"].count("Extra note.") == 2


def test_parse_yaml_text_roundtrip():
    result, _ = _build_base_model()
    yaml_text = ob.to_yaml(result.model)
    reparsed = ob.parse_yaml_text(yaml_text)
    assert reparsed == result.model


def test_parse_yaml_text_rejects_non_mapping():
    with pytest.raises(ValueError):
        ob.parse_yaml_text("- just\n- a\n- list\n")


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
        tables=tables,
        relationships=relationships,
    )
    assert len(result.warnings) == 1
    assert "relationships" not in result.model["semantic_model"][0]
