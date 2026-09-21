"""Tests for ontology derivation from the FACT/DIM semantic sample model."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ontology_builder as ont
import ossie_builder as ob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(ROOT, "sample_data")
SCHEMA = os.path.join(ROOT, "schema", "ontology.json")


def _load(name):
    path = os.path.join(SAMPLE, name)
    with open(path, "rb") as f:
        return ob.load_tabular_bytes(name, f.read())


@pytest.fixture(scope="module")
def account_position_model():
    tables = ob.parse_metadata(_load("01_table_column_metadata.csv"))
    enrichment = ob.parse_metrics_synonyms_extensions(_load("02_metrics_synonyms_extensions.csv"))
    relationships = ob.parse_relationships(_load("03_relationships.csv"))
    result = ob.build_semantic_model(
        model_name="account_position_model",
        model_description="Investment account and position semantic model",
        tables=tables,
        relationships=relationships,
        metrics=enrichment.metrics,
        field_synonyms=enrichment.field_synonyms,
        dataset_extensions=enrichment.dataset_extensions,
        field_extensions=enrichment.field_extensions,
    )
    return result.model


def test_dataset_to_concept_names():
    assert ont.dataset_to_concept_name("DIM_CLIENT") == "Client"
    assert ont.dataset_to_concept_name("FACT_POSITION") == "Position"
    assert ont.dataset_to_concept_name("DIM_DATE") == "CalendarDay"


def test_build_ontology_entities_and_verbs(account_position_model):
    doc = ont.build_ontology_from_semantic_model(account_position_model)
    assert doc["version"] == "0.2.0.dev0"
    by_name = {c["concept"]: c for c in doc["ontology"]}
    for entity in ("Client", "Account", "Security", "CalendarDay", "Position"):
        assert entity in by_name
        assert by_name[entity]["type"] == "EntityType"

    account_rels = {r["name"]: r for r in by_name["Account"]["relationships"]}
    assert "owned_by" in account_rels
    assert account_rels["owned_by"]["roles"][0]["concept"] == "Client"
    assert "{Account} is owned by {Client}" in account_rels["owned_by"]["verbalizes"]

    pos_rels = {r["name"]: r for r in by_name["Position"]["relationships"]}
    assert pos_rels["belongs_to_account"]["roles"][0]["concept"] == "Account"
    assert pos_rels["holds_security"]["roles"][0]["concept"] == "Security"
    assert pos_rels["as_of"]["roles"][0]["concept"] == "CalendarDay"
    assert by_name["Position"]["identify_by"] == ["account", "security", "as_of_date"]


def test_build_ontology_includes_mappings(account_position_model):
    doc = ont.build_ontology_from_semantic_model(account_position_model)
    assert "ontology_mappings" in doc
    om = doc["ontology_mappings"][0]
    assert "semantic_model" in om
    assert om["semantic_model"]["name"] == "account_position_model"
    concepts = {m["concept"] for m in om["concept_mappings"]}
    assert concepts >= {"Client", "Account", "Position", "CalendarDay", "Security"}
    client_map = next(m for m in om["concept_mappings"] if m["concept"] == "Client")
    assert client_map["object_mappings"][0]["expression"] == "DIM_CLIENT.CLIENT_ID"


def test_ontology_validates_against_schema(account_position_model):
    doc = ont.build_ontology_from_semantic_model(account_position_model)
    errs = ont.validate_ontology(doc, SCHEMA)
    assert errs == []


def test_sample_ontology_yaml_roundtrips(account_position_model):
    doc = ont.build_ontology_from_semantic_model(account_position_model)
    text = ont.ontology_to_yaml(doc)
    assert "concept: Client" in text
    assert "verbalizes:" in text
    assert "ontology_mappings:" in text
    parsed = __import__("yaml").safe_load(text)
    assert parsed["name"] == doc["name"]
    assert ont.validate_ontology(parsed, SCHEMA) == []


def test_bundled_sample_ontology_file_is_valid():
    path = os.path.join(SAMPLE, "06_account_position_ontology.yaml")
    assert os.path.isfile(path)
    with open(path, encoding="utf-8") as f:
        doc = __import__("yaml").safe_load(f)
    assert doc["version"] == "0.2.0.dev0"
    errs = ont.validate_ontology(doc, SCHEMA)
    assert errs == []
