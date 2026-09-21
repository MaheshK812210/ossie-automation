"""Build an Apache Ossie **ontology** (+ ontology mappings) from a logical
Ossie **semantic model**.

Spec: https://github.com/apache/ossie/blob/main/ontology/ontology.md
Schema: schema/ontology.json (bundled from apache/ossie).

This is separate from Enrich/SPOKE (which only add ``ai_context`` /
``custom_extensions`` on the semantic model). An ontology declares
business **concepts**, verbalized **relationships**, and **mappings**
from datasets/fields onto those concepts.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

ONTOLOGY_VERSION = "0.2.0.dev0"

# Built-in value types from the ontology spec — ValueTypes must extend one.
_DATATYPE_TO_BUILTIN = {
    "String": "String",
    "Integer": "Integer",
    "Decimal": "Decimal",
    "Float": "Float",
    "Boolean": "Boolean",
    "Date": "Date",
    "Time": "DateTime",
    "DateTime": "DateTime",
    "DateTimeTz": "DateTime",
    "Opaque": "String",
}

# Dataset name → ontology EntityType concept (Date is a built-in ValueType).
_DATASET_CONCEPT_OVERRIDES = {
    "DIM_DATE": "CalendarDay",
    "DATE": "CalendarDay",
}


def _pascal(name: str) -> str:
    parts = re.split(r"[_\s]+", (name or "").strip())
    return "".join(p[:1].upper() + p[1:].lower() for p in parts if p)


def dataset_to_concept_name(dataset_name: str) -> str:
    if dataset_name in _DATASET_CONCEPT_OVERRIDES:
        return _DATASET_CONCEPT_OVERRIDES[dataset_name]
    n = dataset_name
    for prefix in ("DIM_", "FACT_", "DIM", "FACT"):
        if n.upper().startswith(prefix):
            n = n[len(prefix) :]
            break
    return _pascal(n) or dataset_name


def field_to_value_type_name(field_name: str) -> str:
    return _pascal(field_name)


def _sm(model: Dict[str, Any]) -> Dict[str, Any]:
    entries = model.get("semantic_model") or []
    if not entries:
        raise ValueError("Model has no semantic_model entry.")
    return entries[0]


def _fk_columns(sm: Dict[str, Any]) -> Set[Tuple[str, str]]:
    """(table, column) pairs that participate as relationship endpoints."""
    out: Set[Tuple[str, str]] = set()
    for rel in sm.get("relationships") or []:
        ft, tt = rel.get("from"), rel.get("to")
        for c in rel.get("from_columns") or []:
            out.add((ft, c))
        for c in rel.get("to_columns") or []:
            out.add((tt, c))
    return out


def _pk_fields(dataset: Dict[str, Any]) -> List[str]:
    return list(dataset.get("primary_key") or [])


def _rel_verb(from_concept: str, to_concept: str, description: str = "") -> str:
    if description:
        # Soften description into a verbalization template when possible.
        return (
            f"{{{from_concept}}} relates to {{{to_concept}}} "
            f"({description.rstrip('.')})"
        )
    return f"{{{from_concept}}} relates to {{{to_concept}}}"


def build_ontology_from_semantic_model(
    model: Dict[str, Any],
    *,
    ontology_name: Optional[str] = None,
    include_attribute_relationships: bool = True,
    include_mappings: bool = True,
) -> Dict[str, Any]:
    """Derive an Ossie ontology document from a semantic model.

    - Each dataset → ``EntityType`` concept (DIM_/FACT_ prefixes stripped)
    - Primary-key fields → ``ValueType`` + ``identify_by`` relationship
    - Non-key, non-FK fields → attribute relationships to ValueTypes (optional)
    - Semantic FK relationships → verbalized ontology relationships
    - Optional ``ontology_mappings`` embed the logical SemanticModel +
      concept/object/link mappings (per ontology.json)
    """
    sm = _sm(model)
    fk_cols = _fk_columns(sm)
    datasets = sm.get("datasets") or []
    ds_by_name = {d["name"]: d for d in datasets}
    concept_by_dataset = {d["name"]: dataset_to_concept_name(d["name"]) for d in datasets}

    value_types: Dict[str, Dict[str, Any]] = {}
    entity_components: Dict[str, Dict[str, Any]] = {}

    def ensure_value_type(field: Dict[str, Any]) -> str:
        vt_name = field_to_value_type_name(field["name"])
        if vt_name in (
            "Any",
            "Boolean",
            "Date",
            "DateTime",
            "Decimal",
            "Float",
            "Integer",
            "String",
        ):
            vt_name = f"{vt_name}Value"
        if vt_name not in value_types:
            builtin = _DATATYPE_TO_BUILTIN.get(field.get("datatype") or "String", "String")
            comp: Dict[str, Any] = {
                "concept": vt_name,
                "type": "ValueType",
                "extends": [builtin],
            }
            if field.get("description"):
                comp["description"] = field["description"]
            value_types[vt_name] = comp
        return vt_name

    # --- Entity concepts + identify_by / attributes ---
    for dataset in datasets:
        ds_name = dataset["name"]
        concept = concept_by_dataset[ds_name]
        fields_by_name = {f["name"]: f for f in dataset.get("fields") or []}
        pks = _pk_fields(dataset)
        relationships: List[Dict[str, Any]] = []
        identify_by: List[str] = []

        for pk in pks:
            field = fields_by_name.get(pk)
            if not field:
                continue
            vt = ensure_value_type(field)
            rel_name = "id" if len(pks) == 1 else pk.lower()
            # Prefer short names for single-PK identifiers.
            if len(pks) == 1:
                rel_name = "id"
            else:
                rel_name = re.sub(r"_id$", "", pk.lower()) or pk.lower()
            relationships.append(
                {
                    "name": rel_name,
                    "roles": [{"concept": vt}],
                    "multiplicity": "OneToOne" if len(pks) == 1 else "ManyToOne",
                    "verbalizes": [
                        f"{{{concept}}} is identified by {{{vt}}}"
                        if len(pks) == 1
                        else f"{{{concept}}} has {{{vt}}} as part of its identity"
                    ],
                }
            )
            identify_by.append(rel_name)

        if include_attribute_relationships:
            for field in dataset.get("fields") or []:
                fname = field["name"]
                if fname in pks:
                    continue
                if (ds_name, fname) in fk_cols:
                    continue
                vt = ensure_value_type(field)
                attr_name = re.sub(r"_id$", "", fname.lower())
                attr_name = attr_name or fname.lower()
                # Avoid clashing with identify_by names.
                existing = {r["name"] for r in relationships}
                if attr_name in existing:
                    attr_name = f"has_{attr_name}"
                relationships.append(
                    {
                        "name": attr_name,
                        "description": field.get("description") or "",
                        "roles": [{"concept": vt}],
                        "multiplicity": "ManyToOne",
                        "verbalizes": [f"{{{concept}}} has {{{vt}}}"],
                    }
                )
                if not relationships[-1]["description"]:
                    del relationships[-1]["description"]

        entity: Dict[str, Any] = {
            "concept": concept,
            "type": "EntityType",
            "extends": ["Any"],
        }
        if dataset.get("description"):
            entity["description"] = dataset["description"]
        else:
            asset = None
            for ext in dataset.get("custom_extensions") or []:
                if ext.get("vendor_name") == "COMMON":
                    try:
                        import json

                        data = json.loads(ext.get("data") or "{}")
                        asset = data.get("asset_type")
                    except Exception:  # noqa: BLE001
                        asset = None
            if asset:
                entity["description"] = f"{asset} from dataset {ds_name}"
            else:
                entity["description"] = f"Business entity backed by dataset {ds_name}"
        if identify_by:
            entity["identify_by"] = identify_by
        if relationships:
            entity["relationships"] = relationships
        entity_components[concept] = entity

    # --- FK relationships between entities ---
    # Prefer attaching the relationship under the "from" (many) side concept.
    for rel in sm.get("relationships") or []:
        from_ds, to_ds = rel.get("from"), rel.get("to")
        if from_ds not in concept_by_dataset or to_ds not in concept_by_dataset:
            continue
        from_c = concept_by_dataset[from_ds]
        to_c = concept_by_dataset[to_ds]
        # Name: owned_by / belongs_to / holds — derive from tables.
        rel_name = (rel.get("name") or f"{from_ds}_to_{to_ds}").lower()
        rel_name = re.sub(r"^fact_|^dim_", "", rel_name)
        rel_name = re.sub(r"_to_", "_", rel_name)
        # Shorter friendly names for the sample model.
        friendly = {
            "position_account": "belongs_to_account",
            "fact_position_to_account": "belongs_to_account",
            "position_security": "holds_security",
            "fact_position_to_security": "holds_security",
            "position_date": "as_of",
            "fact_position_to_date": "as_of",
            "account_client": "owned_by",
            "account_to_client": "owned_by",
        }
        key = (rel.get("name") or "").lower()
        rel_name = friendly.get(key, friendly.get(rel_name, rel_name))

        desc = rel.get("description") or ""
        # Better verbalizations for known sample edges.
        verbal_map = {
            "belongs_to_account": f"{{{from_c}}} belongs to {{{to_c}}}",
            "holds_security": f"{{{from_c}}} holds {{{to_c}}}",
            "as_of": f"{{{from_c}}} is as of {{{to_c}}}",
            "owned_by": f"{{{from_c}}} is owned by {{{to_c}}}",
        }
        verbal = verbal_map.get(rel_name) or _rel_verb(from_c, to_c, desc)

        ont_rel: Dict[str, Any] = {
            "name": rel_name,
            "roles": [{"concept": to_c}],
            "multiplicity": "ManyToOne",
            "verbalizes": [verbal],
        }
        if desc:
            ont_rel["description"] = desc

        entity_components[from_c].setdefault("relationships", []).append(ont_rel)

    # Stable order: value types first, then entities in dataset order.
    ontology_list: List[Dict[str, Any]] = list(value_types.values())
    for dataset in datasets:
        ontology_list.append(entity_components[concept_by_dataset[dataset["name"]]])

    name = ontology_name or f"{sm.get('name', 'model')}_ontology"
    doc: Dict[str, Any] = {
        "version": ONTOLOGY_VERSION,
        "name": name,
        "description": (
            sm.get("description")
            or f"Ontology derived from semantic model {sm.get('name', '')}"
        ).strip(),
        "ontology": ontology_list,
    }

    # Carry semantic-model AI context up when present.
    if sm.get("ai_context"):
        doc["ai_context"] = copy.deepcopy(sm["ai_context"])

    if include_mappings:
        doc["ontology_mappings"] = [
            build_ontology_map(model, concept_by_dataset=concept_by_dataset)
        ]

    return doc


def build_ontology_map(
    model: Dict[str, Any],
    *,
    concept_by_dataset: Optional[Dict[str, str]] = None,
    map_name: Optional[str] = None,
) -> Dict[str, Any]:
    """One ``OntologyMap``: embedded SemanticModel + concept_mappings."""
    sm = copy.deepcopy(_sm(model))
    datasets = sm.get("datasets") or []
    if concept_by_dataset is None:
        concept_by_dataset = {d["name"]: dataset_to_concept_name(d["name"]) for d in datasets}
    fk_cols = _fk_columns(sm)

    concept_mappings: List[Dict[str, Any]] = []
    for dataset in datasets:
        ds_name = dataset["name"]
        concept = concept_by_dataset[ds_name]
        fields_by_name = {f["name"]: f for f in dataset.get("fields") or []}
        pks = _pk_fields(dataset)

        # Object mapping: simple PK expression or compound referent_mappings.
        if len(pks) == 1:
            object_mappings: List[Dict[str, Any]] = [
                {"expression": f"{ds_name}.{pks[0]}"}
            ]
            id_rel_name = "id"
        elif len(pks) > 1:
            refs = []
            for pk in pks:
                rel_name = re.sub(r"_id$", "", pk.lower()) or pk.lower()
                refs.append(
                    {
                        "relationship": rel_name,
                        "expression": f"{ds_name}.{pk}",
                    }
                )
            object_mappings = [{"referent_mappings": refs}]
            id_rel_name = None
        else:
            # No PK — map first field if any.
            fields = dataset.get("fields") or []
            if not fields:
                continue
            object_mappings = [{"expression": f"{ds_name}.{fields[0]['name']}"}]
            id_rel_name = None

        link_mappings: List[Dict[str, Any]] = []
        # Identifier link (unary/binary as per ontology.md examples).
        if id_rel_name and len(pks) == 1:
            vt = field_to_value_type_name(pks[0])
            if vt in (
                "Any",
                "Boolean",
                "Date",
                "DateTime",
                "Decimal",
                "Float",
                "Integer",
                "String",
            ):
                vt = f"{vt}Value"
            link_mappings.append(
                {
                    "object_mapping": {"expression": f"{ds_name}.{pks[0]}"},
                    "relationship": id_rel_name,
                }
            )

        # Attribute links
        for field in dataset.get("fields") or []:
            fname = field["name"]
            if fname in pks:
                continue
            if (ds_name, fname) in fk_cols:
                continue
            attr_name = re.sub(r"_id$", "", fname.lower()) or fname.lower()
            link_mappings.append(
                {
                    "object_mapping": {"expression": f"{ds_name}.{fname}"},
                    "relationship": attr_name,
                }
            )

        # FK entity links
        for rel in sm.get("relationships") or []:
            if rel.get("from") != ds_name:
                continue
            to_ds = rel.get("to")
            to_concept = concept_by_dataset.get(to_ds)
            if not to_concept:
                continue
            from_cols = rel.get("from_columns") or []
            expr_col = from_cols[0] if from_cols else None
            if not expr_col:
                continue
            key = (rel.get("name") or "").lower()
            friendly = {
                "fact_position_to_account": "belongs_to_account",
                "fact_position_to_security": "holds_security",
                "fact_position_to_date": "as_of",
                "account_to_client": "owned_by",
            }
            rel_name = friendly.get(key, key)
            link_mappings.append(
                {
                    "object_mapping": {
                        "concept": to_concept,
                        "expression": f"{ds_name}.{expr_col}",
                    },
                    "relationship": rel_name,
                }
            )

        cm: Dict[str, Any] = {
            "concept": concept,
            "object_mappings": object_mappings,
        }
        if link_mappings:
            cm["link_mappings"] = link_mappings
        concept_mappings.append(cm)

    return {
        "name": map_name or f"{sm.get('name', 'model')}_logical_map",
        "description": (
            f"Maps semantic model `{sm.get('name')}` datasets/fields onto ontology concepts."
        ),
        "semantic_model": sm,
        "concept_mappings": concept_mappings,
    }


def ontology_to_yaml(doc: Dict[str, Any]) -> str:
    header = (
        "# Generated Apache Ossie ontology (concepts + mappings)\n"
        "# Spec: https://github.com/apache/ossie/blob/main/ontology/ontology.md\n"
        f"# yaml-language-server: $schema=./ontology.json\n"
    )

    class _Dumper(yaml.SafeDumper):
        pass

    def _str_presenter(dumper: yaml.Dumper, data: str):
        if "\n" in data:
            return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
        return dumper.represent_scalar("tag:yaml.org,2002:str", data)

    _Dumper.add_representer(str, _str_presenter)
    body = yaml.dump(
        doc,
        Dumper=_Dumper,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=100,
    )
    return header + body


def validate_ontology(doc: Dict[str, Any], schema_path: str) -> List[str]:
    """Validate against schema/ontology.json. Remote $refs may be skipped
    offline — structural checks still run on the local schema body."""
    import json
    from pathlib import Path

    try:
        import jsonschema
        from jsonschema import Draft202012Validator
    except ImportError:
        return ["jsonschema is not installed"]

    schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    # Soften remote $refs that break offline validation of ai_context /
    # embedded semantic_model — validate core ontology shape locally.
    def _strip_remote_refs(node: Any) -> Any:
        if isinstance(node, dict):
            if set(node.keys()) == {"$ref"} and str(node["$ref"]).startswith("http"):
                return {}
            return {k: _strip_remote_refs(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_strip_remote_refs(v) for v in node]
        return node

    local_schema = _strip_remote_refs(schema)
    # After stripping, OntologyMap.semantic_model becomes unrestricted;
    # required keys still enforced.
    validator = Draft202012Validator(local_schema)
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
    return [f"{'/'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in errors]
