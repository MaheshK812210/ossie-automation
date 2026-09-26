"""Bridge between this app's Ossie models and the official Apache
``ossie_microsoft`` converter (vendored under ``vendor/apache-ossie-microsoft``).

Official package: https://github.com/apache/ossie/tree/main/converters/microsoft

Document shapes
---------------
- **This app** stores models as ``{"semantic_model": [ {name, datasets, ...} ]}``.
- **ossie_microsoft** expects a *flat* document with ``version``, ``name``,
  ``datasets``, ``relationships``, ``metrics`` at the root (no
  ``semantic_model`` wrapper).

This module unwraps/wraps those shapes, captures conversion warnings, and
optionally packages TMSL into a PBIP zip / injects Snowflake M partitions.
"""

from __future__ import annotations

import io
import json
import warnings
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ossie_microsoft import (
    convert_ossie_to_semantic_model,
    convert_semantic_model_to_ossie,
)

import powerbi_export as pbe

OSSIE_SPEC_VERSION = "0.2.0.dev0"


@dataclass
class ConversionResult:
    """Result of an Ossie ↔ Power BI conversion via ossie_microsoft."""

    warnings: List[str] = field(default_factory=list)
    # Ossie → Power BI
    tmsl: Optional[Dict[str, Any]] = None
    bim_json: Optional[str] = None
    pbip_zip_bytes: Optional[bytes] = None
    # Power BI → Ossie
    ossie_flat: Optional[Dict[str, Any]] = None
    ossie_yaml: Optional[str] = None
    app_model: Optional[Dict[str, Any]] = None


def app_model_to_flat_document(model: Dict[str, Any]) -> Dict[str, Any]:
    """Unwrap ``{"semantic_model": [sm]}`` into the flat Ossie document
    shape expected by ``ossie_microsoft``."""
    entries = model.get("semantic_model") or []
    if not entries:
        raise ValueError("Model has no semantic_model entry to convert.")
    sm = entries[0]
    flat: Dict[str, Any] = {
        "version": OSSIE_SPEC_VERSION,
        "name": sm.get("name") or "semantic_model",
    }
    if sm.get("description"):
        flat["description"] = sm["description"]
    if sm.get("ai_context") is not None:
        flat["ai_context"] = sm["ai_context"]
    flat["datasets"] = sm.get("datasets") or []
    if sm.get("relationships"):
        flat["relationships"] = sm["relationships"]
    if sm.get("metrics"):
        flat["metrics"] = sm["metrics"]
    if sm.get("custom_extensions"):
        flat["custom_extensions"] = sm["custom_extensions"]
    return flat


def flat_document_to_app_model(flat: Dict[str, Any]) -> Dict[str, Any]:
    """Wrap a flat Ossie document into this app's session-model shape."""
    sm = {k: v for k, v in flat.items() if k != "version"}
    if "name" not in sm:
        sm["name"] = "imported_model"
    if "datasets" not in sm:
        sm["datasets"] = []
    return {"semantic_model": [sm]}


def flat_document_to_yaml(flat: Dict[str, Any]) -> str:
    return yaml.safe_dump(flat, sort_keys=False, allow_unicode=True)


def _capture_warnings(fn, *args, **kwargs) -> Tuple[Any, List[str]]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = fn(*args, **kwargs)
    msgs = []
    for w in caught:
        msgs.append(str(w.message))
    return result, msgs


def _apply_snowflake_partitions(
    tmsl: Dict[str, Any],
    app_model: Dict[str, Any],
    data_source: Dict[str, Any],
) -> Dict[str, Any]:
    """Replace table partitions with Snowflake.Databases M from our exporter."""
    sm = app_model["semantic_model"][0]
    ds_by_name = {d["name"]: d for d in sm.get("datasets") or []}
    out = json.loads(json.dumps(tmsl))  # deep copy via JSON
    for table in out.get("model", {}).get("tables") or []:
        ds = ds_by_name.get(table["name"])
        if not ds:
            continue
        m_expr = pbe._build_partition_m_expression(
            table["name"], ds.get("source", table["name"]), data_source
        )
        table["partitions"] = [
            {
                "name": f"{table['name']}-partition",
                "mode": "import",
                "source": {"type": "m", "expression": m_expr},
            }
        ]
    return out


def package_tmsl_as_pbip_zip(tmsl: Dict[str, Any], project_name: str) -> bytes:
    """Wrap a TMSL ``model.bim`` mapping in a minimal openable ``.pbip`` zip
    (TMSL-only SemanticModel + blank Report), reusing this app's packaging."""
    safe = pbe._safe_project_name(project_name)
    sm_folder = f"{safe}.SemanticModel"
    bim_bytes = json.dumps(tmsl, indent=2, ensure_ascii=False).encode("utf-8")
    files: Dict[str, bytes] = {
        f"{sm_folder}/.platform": pbe.build_platform_bytes(safe),
        f"{sm_folder}/definition.pbism": pbe.build_pbism_bytes(),
        f"{sm_folder}/model.bim": bim_bytes,
        f"{sm_folder}/README.md": (
            f"# {safe} — Power BI Semantic Model (TMSL)\n\n"
            "Produced by the official Apache ``ossie_microsoft`` converter, "
            "packaged as a Power BI Project by this app.\n"
        ).encode("utf-8"),
    }
    files.update(pbe.build_report_project_files(safe, sm_folder))
    report_folder = f"{safe}.Report"
    files[f"{safe}.pbip"] = pbe.build_pbip_manifest_bytes(report_folder)
    files["README.md"] = (
        f"# {safe} Power BI Project\n\n"
        f"Unzip and open `{safe}.pbip` in Power BI Desktop "
        "(*File → Open → Power BI Project*).\n"
        "Semantic model converted with Apache ossie_microsoft.\n"
    ).encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, data in sorted(files.items()):
            zf.writestr(path, data)
    return buf.getvalue()


def ossie_to_powerbi(
    app_model: Dict[str, Any],
    *,
    project_name: str = "semantic_model",
    data_source: Optional[Dict[str, Any]] = None,
    package_pbip: bool = True,
) -> ConversionResult:
    """Convert this app's Ossie model → Power BI TMSL via ossie_microsoft.

    ``data_source`` with ``type: snowflake`` optionally rewrites partitions to
    ``Snowflake.Databases(...)`` M after the official conversion.
    """
    flat = app_model_to_flat_document(app_model)
    flat_yaml = flat_document_to_yaml(flat)
    tmsl, warns = _capture_warnings(convert_ossie_to_semantic_model, flat_yaml)
    if not isinstance(tmsl, dict):
        raise TypeError("Expected TMSL mapping from convert_ossie_to_semantic_model")

    if data_source and data_source.get("type") == "snowflake":
        tmsl = _apply_snowflake_partitions(tmsl, app_model, data_source)
        warns.append(
            "Partitions rewritten to Snowflake.Databases(...) M using this app's "
            "Snowflake settings (post-process after ossie_microsoft)."
        )

    bim_json = json.dumps(tmsl, indent=2, ensure_ascii=False)
    zip_bytes = package_tmsl_as_pbip_zip(tmsl, project_name) if package_pbip else None
    return ConversionResult(
        warnings=warns,
        tmsl=tmsl,
        bim_json=bim_json,
        pbip_zip_bytes=zip_bytes,
    )


def powerbi_to_ossie(bim: Dict[str, Any] | str) -> ConversionResult:
    """Convert a Power BI ``model.bim`` (dict or JSON text) → this app's Ossie model."""
    yaml_str, warns = _capture_warnings(convert_semantic_model_to_ossie, bim)
    flat = yaml.safe_load(yaml_str)
    if not isinstance(flat, dict):
        raise ValueError("Converter returned non-mapping Ossie YAML")
    app_model = flat_document_to_app_model(flat)
    return ConversionResult(
        warnings=warns,
        ossie_flat=flat,
        ossie_yaml=yaml_str,
        app_model=app_model,
    )


def load_bim_bytes(raw: bytes) -> Dict[str, Any]:
    """Parse uploaded ``model.bim`` / JSON bytes into a TMSL mapping."""
    text = raw.decode("utf-8-sig")
    return json.loads(text)
