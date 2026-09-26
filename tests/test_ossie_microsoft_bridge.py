"""Tests for the ossie_microsoft bridge (Ossie ↔ Power BI both directions)."""

from __future__ import annotations

import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ossie_builder as ob
import ossie_microsoft_bridge as omb

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


def test_app_model_to_flat_and_back(account_position_model):
    flat = omb.app_model_to_flat_document(account_position_model)
    assert "version" in flat
    assert flat["name"] == "account_position_model"
    assert "semantic_model" not in flat
    assert len(flat["datasets"]) >= 1

    wrapped = omb.flat_document_to_app_model(flat)
    assert "semantic_model" in wrapped
    assert wrapped["semantic_model"][0]["name"] == flat["name"]
    assert len(wrapped["semantic_model"][0]["datasets"]) == len(flat["datasets"])


def test_flat_document_rejects_empty_model():
    with pytest.raises(ValueError, match="no semantic_model"):
        omb.app_model_to_flat_document({"semantic_model": []})


def test_ossie_to_powerbi_produces_tmsl_and_pbip(account_position_model):
    result = omb.ossie_to_powerbi(
        account_position_model,
        project_name="account_position_model",
        package_pbip=True,
    )
    assert result.tmsl is not None
    assert "model" in result.tmsl
    tables = result.tmsl["model"]["tables"]
    assert len(tables) >= 1
    table_names = {t["name"] for t in tables}
    assert "FACT_POSITION" in table_names or any("POSITION" in n.upper() for n in table_names)

    assert result.bim_json
    parsed = json.loads(result.bim_json)
    assert parsed["model"]["tables"]

    assert result.pbip_zip_bytes
    with zipfile.ZipFile(__import__("io").BytesIO(result.pbip_zip_bytes)) as zf:
        names = zf.namelist()
        assert any(n.endswith("model.bim") for n in names)
        assert any(n.endswith(".pbip") for n in names)


def test_ossie_to_powerbi_snowflake_partitions(account_position_model):
    data_source = {
        "type": "snowflake",
        "account": "myorg-myaccount.snowflakecomputing.com",
        "warehouse": "COMPUTE_WH",
        "role": "ANALYST",
    }
    result = omb.ossie_to_powerbi(
        account_position_model,
        project_name="account_position_model",
        data_source=data_source,
        package_pbip=False,
    )
    assert result.tmsl is not None
    expr_blob = json.dumps(result.tmsl)
    assert "Snowflake.Databases" in expr_blob
    assert any("Snowflake" in w for w in result.warnings)


def test_powerbi_to_ossie_round_trip(account_position_model):
    forward = omb.ossie_to_powerbi(
        account_position_model, project_name="rt", package_pbip=False
    )
    back = omb.powerbi_to_ossie(forward.tmsl)
    assert back.app_model is not None
    assert back.ossie_yaml
    sm = back.app_model["semantic_model"][0]
    assert sm.get("datasets")
    names = {d["name"] for d in sm["datasets"]}
    # Round-trip should preserve core fact/dim table names
    assert any("POSITION" in n.upper() or "FACT" in n.upper() for n in names)


def test_load_bim_bytes_round_trip(account_position_model):
    forward = omb.ossie_to_powerbi(
        account_position_model, project_name="rt", package_pbip=False
    )
    bim = omb.load_bim_bytes(forward.bim_json.encode("utf-8"))
    assert bim["model"]["tables"]
    back = omb.powerbi_to_ossie(bim)
    assert back.app_model["semantic_model"][0]["datasets"]
