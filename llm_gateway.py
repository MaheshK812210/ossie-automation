"""LLM Gateway client for SPOKE SQL enrichment.

This project does **not** host an LLM. It calls an external LLM Gateway
that already knows how to turn SQL into Ossie-friendly enrichment
instructions (the same gateway used elsewhere for text-to-SQL).

Configure with ``LLM_GATEWAY_URL`` (and optional ``LLM_GATEWAY_TOKEN``).
When no URL is set, a deterministic local fallback still returns a
well-formed instruction payload so the SPOKE UI and tests work offline —
production should always point at the real gateway.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


DEFAULT_TIMEOUT_SEC = 60


class LLMGatewayError(RuntimeError):
    """Raised when the gateway is configured but the call fails."""


def gateway_configured() -> bool:
    return bool(os.environ.get("LLM_GATEWAY_URL", "").strip())


def _gateway_url() -> str:
    return os.environ.get("LLM_GATEWAY_URL", "").strip().rstrip("/")


def _gateway_token() -> str:
    return os.environ.get("LLM_GATEWAY_TOKEN", "").strip()


def _post_json(url: str, payload: Dict[str, Any], token: str = "") -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        raise LLMGatewayError(f"LLM gateway HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise LLMGatewayError(f"LLM gateway unreachable: {e.reason}") from e
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError as e:
        raise LLMGatewayError(f"LLM gateway returned non-JSON: {raw[:200]}") from e
    if not isinstance(data, dict):
        raise LLMGatewayError("LLM gateway response must be a JSON object")
    return data


def _local_fallback_instruction(sql_text: str, filename: str) -> str:
    """Offline stand-in that mimics what the gateway is expected to return:
    a natural-language instruction derived from the SQL, for model-level
    AI enrichment. Not a real LLM — only used when ``LLM_GATEWAY_URL`` is
    unset.
    """
    compact = re.sub(r"\s+", " ", sql_text).strip()
    preview = compact if len(compact) <= 800 else compact[:800] + "…"
    name = filename or "attached.sql"
    return (
        f"When answering questions, prefer the business logic encoded in "
        f"SQL file `{name}`. Treat it as approved query semantics for this "
        f"semantic model (filters, joins, and measures implied by the SQL). "
        f"SQL summary: {preview}"
    )


def parse_sql_to_enrichment(
    sql_text: str,
    *,
    filename: str = "",
    model_name: str = "",
) -> Dict[str, Any]:
    """Always parse ``sql_text`` through the LLM gateway contract.

    Returns a JSON-serializable dict that becomes
    ``custom_extensions[].data`` at the Ossie **model** level, including
    at least an ``instruction`` string.

    Expected gateway endpoint: ``POST {LLM_GATEWAY_URL}/v1/sql-to-instruction``
    with body ``{"sql": "...", "filename": "...", "model_name": "..."}``
    and JSON response containing ``instruction`` (string). Extra fields
    from the gateway are preserved inside the enrichment payload.
    """
    sql_text = (sql_text or "").strip()
    if not sql_text:
        raise ValueError("SQL file is empty — nothing to parse.")

    filename = filename or "attached.sql"
    url = _gateway_url()
    gateway_payload: Optional[Dict[str, Any]] = None

    if url:
        endpoint = f"{url}/v1/sql-to-instruction"
        gateway_payload = _post_json(
            endpoint,
            {"sql": sql_text, "filename": filename, "model_name": model_name or ""},
            token=_gateway_token(),
        )
        instruction = (
            gateway_payload.get("instruction")
            or gateway_payload.get("instructions")
            or ""
        )
        if not str(instruction).strip():
            raise LLMGatewayError(
                "LLM gateway response missing required 'instruction' field."
            )
        instruction = str(instruction).strip()
        source = "llm_gateway"
    else:
        instruction = _local_fallback_instruction(sql_text, filename)
        source = "llm_gateway_fallback"

    enrichment: Dict[str, Any] = {
        "enrichment_type": "sql_spoke",
        "source": source,
        "sql_filename": filename,
        "instruction": instruction,
        "sql": sql_text,
    }
    if gateway_payload:
        # Preserve any extra structured fields the gateway returned
        # (e.g. tables_referenced, metrics_hint) without clobbering ours.
        for key, value in gateway_payload.items():
            if key in ("instruction", "instructions"):
                continue
            if key not in enrichment:
                enrichment[key] = value
    return enrichment
