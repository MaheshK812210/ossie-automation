"""Tests for SPOKE SQL → LLM gateway → model-level custom_extension enrichment."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm_gateway as llmgw
import ossie_builder as ob


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_SQL = os.path.join(ROOT, "sample_data", "05_spoke_market_value_by_client.sql")


@pytest.fixture
def minimal_model():
    return {
        "semantic_model": [
            {
                "name": "account_position_model",
                "description": "test",
                "datasets": [
                    {
                        "name": "FACT_POSITION",
                        "source": "WEALTH_DB.PUBLIC.FACT_POSITION",
                        "fields": [
                            {
                                "name": "MARKET_VALUE",
                                "expression": {
                                    "dialects": [
                                        {"dialect": "ANSI_SQL", "expression": "MARKET_VALUE"}
                                    ]
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    }


def test_parse_sql_to_enrichment_fallback_without_gateway(monkeypatch):
    monkeypatch.delenv("LLM_GATEWAY_URL", raising=False)
    with open(SAMPLE_SQL, encoding="utf-8") as f:
        sql = f.read()
    payload = llmgw.parse_sql_to_enrichment(sql, filename="sample.sql", model_name="m1")
    assert payload["enrichment_type"] == "sql_spoke"
    assert payload["source"] == "llm_gateway_fallback"
    assert payload["sql_filename"] == "sample.sql"
    assert "instruction" in payload and "sample.sql" in payload["instruction"]
    assert "SELECT" in payload["sql"].upper()


def test_parse_sql_to_enrichment_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        llmgw.parse_sql_to_enrichment("   ")


def test_parse_sql_to_enrichment_calls_gateway(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_URL", "https://gateway.example")

    def fake_post(url, payload, token=""):
        assert url.endswith("/v1/sql-to-instruction")
        assert "SUM" in payload["sql"].upper()
        return {
            "instruction": "Prefer client-level market value from this SQL.",
            "tables_referenced": ["FACT_POSITION", "DIM_CLIENT"],
        }

    monkeypatch.setattr(llmgw, "_post_json", fake_post)
    out = llmgw.parse_sql_to_enrichment(
        "SELECT SUM(MARKET_VALUE) FROM FACT_POSITION",
        filename="mv.sql",
        model_name="account_position_model",
    )
    assert out["source"] == "llm_gateway"
    assert out["instruction"] == "Prefer client-level market value from this SQL."
    assert out["tables_referenced"] == ["FACT_POSITION", "DIM_CLIENT"]
    assert out["sql_filename"] == "mv.sql"


def test_parse_sql_to_enrichment_gateway_missing_instruction(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_URL", "https://gateway.example")
    monkeypatch.setattr(llmgw, "_post_json", lambda *a, **k: {"foo": "bar"})
    with pytest.raises(llmgw.LLMGatewayError, match="instruction"):
        llmgw.parse_sql_to_enrichment("SELECT 1", filename="x.sql")


def test_merge_sql_spoke_into_model_appends_extension_and_ai_context(minimal_model, monkeypatch):
    monkeypatch.delenv("LLM_GATEWAY_URL", raising=False)
    enrichment = llmgw.parse_sql_to_enrichment(
        "SELECT SUM(MARKET_VALUE) AS total FROM FACT_POSITION",
        filename="total.sql",
    )
    enriched = ob.merge_sql_spoke_into_model(minimal_model, enrichment)
    # Original untouched
    assert "custom_extensions" not in minimal_model["semantic_model"][0]

    sm = enriched["semantic_model"][0]
    spoke_exts = [e for e in sm["custom_extensions"] if e["vendor_name"] == "SPOKE"]
    assert len(spoke_exts) == 1
    data = json.loads(spoke_exts[0]["data"])
    assert data["enrichment_type"] == "sql_spoke"
    assert data["instruction"]
    assert data["sql_filename"] == "total.sql"
    assert "instructions" in sm["ai_context"]
    assert data["instruction"] in sm["ai_context"]["instructions"]

    # Additive on second pass
    again = ob.merge_sql_spoke_into_model(enriched, enrichment)
    assert len([e for e in again["semantic_model"][0]["custom_extensions"] if e["vendor_name"] == "SPOKE"]) == 2


def test_merge_sql_spoke_requires_instruction(minimal_model):
    with pytest.raises(ValueError, match="instruction"):
        ob.merge_sql_spoke_into_model(minimal_model, {"sql_filename": "x.sql"})
