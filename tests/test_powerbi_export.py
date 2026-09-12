import io
import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ossie_builder as ob
import powerbi_export as pbe

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_DIR = os.path.join(ROOT, "sample_data")


def _load_sample(name):
    path = os.path.join(SAMPLE_DIR, name)
    with open(path, "rb") as f:
        raw = f.read()
    return ob.load_tabular_bytes(name, raw)


@pytest.fixture(scope="module")
def account_position_model():
    metadata_df = _load_sample("01_table_column_metadata.csv")
    enrichment_df = _load_sample("02_metrics_synonyms_extensions.csv")
    rel_df = _load_sample("03_relationships.csv")

    tables = ob.parse_metadata(metadata_df)
    enrichment = ob.parse_metrics_synonyms_extensions(enrichment_df)
    relationships = ob.parse_relationships(rel_df)

    result = ob.build_semantic_model(
        model_name="account_position_model",
        model_description="Account and position semantic model",
        tables=tables,
        relationships=relationships,
        metrics=enrichment.metrics,
        field_synonyms=enrichment.field_synonyms,
        dataset_extensions=enrichment.dataset_extensions,
        field_extensions=enrichment.field_extensions,
    )
    return result.model


@pytest.mark.parametrize(
    "ossie_type,expected",
    [
        ("String", "string"),
        ("Integer", "int64"),
        ("Decimal", "decimal"),
        ("Float", "double"),
        ("Boolean", "boolean"),
        ("Date", "dateTime"),
        ("DateTime", "dateTime"),
        ("DateTimeTz", "dateTime"),
        ("Opaque", "string"),
        (None, "string"),
        ("SomethingUnknown", "string"),
    ],
)
def test_map_datatype_to_tabular(ossie_type, expected):
    assert pbe.map_datatype_to_tabular(ossie_type) == expected


@pytest.mark.parametrize(
    "sql,expected",
    [
        ("SUM(FACT_POSITION.MARKET_VALUE)", "SUM(FACT_POSITION[MARKET_VALUE])"),
        ("COUNT(DISTINCT FACT_POSITION.SECURITY_ID)", "DISTINCTCOUNT(FACT_POSITION[SECURITY_ID])"),
        ("AVG(FACT_POSITION.MARKET_VALUE)", "AVERAGE(FACT_POSITION[MARKET_VALUE])"),
        (
            "SUM(store_sales.ss_ext_sales_price) / COUNT(DISTINCT customer.c_customer_sk)",
            "DIVIDE(SUM(store_sales[ss_ext_sales_price]), DISTINCTCOUNT(customer[c_customer_sk]))",
        ),
        (
            "SUM(store_sales.ss_ext_sales_price) / NULLIF(SUM(store.s_number_employees), 0)",
            "DIVIDE(SUM(store_sales[ss_ext_sales_price]), SUM(store[s_number_employees]))",
        ),
        ("", ""),
    ],
)
def test_sql_to_dax(sql, expected):
    assert pbe.sql_to_dax(sql) == expected


def test_sql_to_dax_leaves_ambiguous_multi_division_untouched():
    expr = "a.x / b.y / c.z"
    out = pbe.sql_to_dax(expr)
    # More than one top-level division is ambiguous to auto-wrap; the dot
    # references still get rewritten but no DIVIDE() wrapping is attempted.
    assert "DIVIDE(" not in out
    assert "a[x]" in out and "b[y]" in out and "c[z]" in out


def test_build_snowflake_m_expression():
    m = pbe.build_snowflake_m_expression(
        "FACT_POSITION", "WEALTH_DB.PUBLIC.FACT_POSITION", "myorg-myaccount.snowflakecomputing.com", "COMPUTE_WH"
    )
    assert 'Snowflake.Databases("myorg-myaccount.snowflakecomputing.com", "COMPUTE_WH")' in m
    assert 'Name="WEALTH_DB", Kind="Database"' in m
    assert 'Name="PUBLIC", Kind="Schema"' in m
    assert 'Name="FACT_POSITION", Kind="Table"' in m
    assert "Role=" not in m


def test_build_snowflake_m_expression_with_role():
    m = pbe.build_snowflake_m_expression(
        "FACT_POSITION",
        "WEALTH_DB.PUBLIC.FACT_POSITION",
        "myorg-myaccount.snowflakecomputing.com",
        "COMPUTE_WH",
        role="ANALYST_ROLE",
    )
    assert '[Role="ANALYST_ROLE"]' in m


def test_build_snowflake_m_expression_falls_back_for_short_source():
    m = pbe.build_snowflake_m_expression("T", "T", "acct.snowflakecomputing.com", "WH")
    assert 'Name="<YOUR_DATABASE>", Kind="Database"' in m
    assert 'Name="<YOUR_SCHEMA>", Kind="Schema"' in m
    assert 'Name="T", Kind="Table"' in m


def test_build_tmsl_model_uses_snowflake_data_source_when_given(account_position_model):
    data_source = {"type": "snowflake", "account": "acct.snowflakecomputing.com", "warehouse": "WH", "role": "R1"}
    tmsl = pbe.build_tmsl_model(account_position_model, data_source=data_source)
    fact = next(t for t in tmsl["model"]["tables"] if t["name"] == "FACT_POSITION")
    m_expr = fact["partitions"][0]["source"]["expression"]
    assert "Snowflake.Databases" in m_expr
    assert '[Role="R1"]' in m_expr
    assert "Sql.Database" not in m_expr


def test_build_tmsl_model_defaults_to_placeholder_source_without_data_source(account_position_model):
    tmsl = pbe.build_tmsl_model(account_position_model)
    fact = next(t for t in tmsl["model"]["tables"] if t["name"] == "FACT_POSITION")
    m_expr = fact["partitions"][0]["source"]["expression"]
    assert "Sql.Database" in m_expr
    assert "Snowflake" not in m_expr


def test_convert_to_powerbi_threads_data_source_through_zip(account_position_model):
    data_source = {"type": "snowflake", "account": "acct.snowflakecomputing.com", "warehouse": "WH"}
    export = pbe.convert_to_powerbi(account_position_model, "account_position_model", data_source=data_source)
    assert "Snowflake.Databases" in export.tmsl_json
    tmdl_fact = export.tmdl_files["definition/tables/FACT_POSITION.tmdl"]
    assert "Snowflake.Databases" in tmdl_fact

    zf = zipfile.ZipFile(io.BytesIO(export.zip_bytes))
    bim_doc = json.loads(zf.read("account_position_model.SemanticModel/model.bim"))
    fact = next(t for t in bim_doc["model"]["tables"] if t["name"] == "FACT_POSITION")
    assert "Snowflake.Databases" in fact["partitions"][0]["source"]["expression"]


def test_build_tmsl_model_structure(account_position_model):
    tmsl = pbe.build_tmsl_model(account_position_model)
    assert tmsl["name"] == "account_position_model"
    assert "compatibilityLevel" in tmsl

    tables = {t["name"]: t for t in tmsl["model"]["tables"]}
    assert set(tables) == {"DIM_CLIENT", "DIM_ACCOUNT", "DIM_SECURITY", "DIM_DATE", "FACT_POSITION"}

    fact = tables["FACT_POSITION"]
    col_names = {c["name"] for c in fact["columns"]}
    assert "MARKET_VALUE" in col_names
    assert fact["partitions"][0]["mode"] == "import"

    measure_names = {m["name"] for m in fact.get("measures", [])}
    assert "total_market_value" in measure_names
    total_mv = next(m for m in fact["measures"] if m["name"] == "total_market_value")
    assert total_mv["expression"] == "SUM(FACT_POSITION[MARKET_VALUE])"

    rels = tmsl["model"]["relationships"]
    rel_names = {r["name"] for r in rels}
    assert "FACT_POSITION_TO_ACCOUNT" in rel_names
    fp_to_acct = next(r for r in rels if r["name"] == "FACT_POSITION_TO_ACCOUNT")
    assert fp_to_acct["fromTable"] == "FACT_POSITION"
    assert fp_to_acct["fromColumn"] == "ACCOUNT_ID"
    assert fp_to_acct["toTable"] == "DIM_ACCOUNT"
    assert fp_to_acct["toColumn"] == "ACCOUNT_ID"


def test_tmsl_to_json_roundtrips(account_position_model):
    tmsl = pbe.build_tmsl_model(account_position_model)
    text = pbe.tmsl_to_json_str(tmsl)
    assert json.loads(text) == tmsl


def test_build_tmdl_files(account_position_model):
    files = pbe.build_tmdl_files(account_position_model)
    assert "definition/model.tmdl" in files
    assert "definition/tables/FACT_POSITION.tmdl" in files
    assert "definition/relationships.tmdl" in files

    fact_tmdl = files["definition/tables/FACT_POSITION.tmdl"]
    assert "table FACT_POSITION" in fact_tmdl
    assert "measure total_market_value = SUM(FACT_POSITION[MARKET_VALUE])" in fact_tmdl
    assert "column MARKET_VALUE" in fact_tmdl

    rel_tmdl = files["definition/relationships.tmdl"]
    assert "fromColumn: FACT_POSITION.ACCOUNT_ID" in rel_tmdl
    assert "toColumn: DIM_ACCOUNT.ACCOUNT_ID" in rel_tmdl


def test_build_pbip_zip_contains_expected_files(account_position_model):
    zb = pbe.build_pbip_zip_bytes(account_position_model, "account_position_model")
    zf = zipfile.ZipFile(io.BytesIO(zb))
    names = set(zf.namelist())
    root = "account_position_model.SemanticModel"
    assert f"{root}/.platform" in names
    assert f"{root}/definition.pbism" in names
    assert f"{root}/definition/model.tmdl" in names
    assert f"{root}/definition/tables/FACT_POSITION.tmdl" in names
    assert f"{root}/model.bim" in names

    platform_doc = json.loads(zf.read(f"{root}/.platform"))
    assert platform_doc["metadata"]["type"] == "SemanticModel"

    bim_doc = json.loads(zf.read(f"{root}/model.bim"))
    assert bim_doc["name"] == "account_position_model"


def test_build_pbip_zip_sanitizes_project_name(account_position_model):
    zb = pbe.build_pbip_zip_bytes(account_position_model, "my project! v1.0")
    zf = zipfile.ZipFile(io.BytesIO(zb))
    names = zf.namelist()
    assert all("!" not in n for n in names)
    assert any(".SemanticModel/.platform" in n for n in names)


def test_generate_synthetic_data_shapes(account_position_model):
    data = pbe.generate_synthetic_data(account_position_model, n_rows=25, seed=1)
    assert set(data.keys()) == {"DIM_CLIENT", "DIM_ACCOUNT", "DIM_SECURITY", "DIM_DATE", "FACT_POSITION"}
    for name, df in data.items():
        assert len(df) == 25
    fact_df = data["FACT_POSITION"]
    assert "MARKET_VALUE" in fact_df.columns


def test_evaluate_metrics_computes_values_for_single_table_metrics(account_position_model):
    data = pbe.generate_synthetic_data(account_position_model, n_rows=30, seed=7)
    results = pbe.evaluate_metrics(account_position_model, data)
    by_name = {r["name"]: r for r in results}

    assert len(results) == 5
    for name in [
        "total_market_value",
        "total_cost_basis",
        "total_unrealized_gain_loss",
        "distinct_securities_held",
        "average_position_value",
    ]:
        assert by_name[name]["error"] is None
        assert by_name[name]["value"] is not None

    assert by_name["distinct_securities_held"]["value"] <= 30


def test_evaluate_metrics_reports_error_for_unjoinable_tables():
    model = {
        "version": "0.2.0.dev0",
        "semantic_model": [
            {
                "name": "isolated_model",
                "datasets": [
                    {"name": "A", "source": "a", "fields": [{"name": "x", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "x"}]}, "datatype": "Integer"}]},
                    {"name": "B", "source": "b", "fields": [{"name": "y", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "y"}]}, "datatype": "Integer"}]},
                ],
                "relationships": [],
                "metrics": [
                    {
                        "name": "cross_metric",
                        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(A.x) / SUM(B.y)"}]},
                    }
                ],
            }
        ],
    }
    data = pbe.generate_synthetic_data(model, n_rows=10)
    results = pbe.evaluate_metrics(model, data)
    assert len(results) == 1
    assert results[0]["error"] is not None
    assert results[0]["value"] is None
