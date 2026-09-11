"""Deploy a generated Power BI semantic model (TMDL) directly into a
Microsoft Fabric workspace via the Fabric REST API -- headlessly, with a
single HTTPS call. No Power BI Desktop, SSMS, or Tabular Editor required.

API reference:
https://learn.microsoft.com/en-us/rest/api/fabric/semanticmodel/items/create-semantic-model

What you need on the Fabric/Power BI side (this module can't provide these
for you -- they depend on your own Microsoft 365/Azure tenant):

1. A **Fabric-enabled workspace** (a Fabric trial capacity, Premium, or
   Premium Per User workspace -- the free/Pro tier does not support this
   API). The workspace's GUID is in its URL in the Fabric/Power BI portal.
2. A **bearer token** for the ``https://api.fabric.microsoft.com`` audience.
   The simplest way to get one interactively, from any terminal (no
   desktop app):

       az login
       az account get-access-token --resource https://api.fabric.microsoft.com --query accessToken -o tsv

   For unattended/CI use, register an Azure AD application and use its
   client-credentials flow instead. Tokens are short-lived (~1 hour) and
   are never stored by this app -- they're used only for the single
   deploy request you trigger.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"


@dataclass
class DeployResult:
    success: bool
    message: str
    item_id: Optional[str] = None
    workspace_url: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None


def _b64(data) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return base64.b64encode(raw).decode("ascii")


def build_definition_parts(
    tmdl_files: Dict[str, str],
    pbism_bytes: bytes,
    platform_bytes: Optional[bytes] = None,
) -> List[Dict[str, str]]:
    """Builds the ``definition.parts`` array the Fabric API expects: every
    file base64-encoded with ``payloadType: InlineBase64``."""
    parts = [{"path": "definition.pbism", "payload": _b64(pbism_bytes), "payloadType": "InlineBase64"}]
    for path, content in tmdl_files.items():
        parts.append({"path": path, "payload": _b64(content), "payloadType": "InlineBase64"})
    if platform_bytes:
        parts.append({"path": ".platform", "payload": _b64(platform_bytes), "payloadType": "InlineBase64"})
    return parts


def create_semantic_model(
    workspace_id: str,
    bearer_token: str,
    display_name: str,
    tmdl_files: Dict[str, str],
    pbism_bytes: bytes,
    platform_bytes: Optional[bytes] = None,
    description: str = "",
    timeout: float = 30.0,
    poll_interval: float = 2.0,
    max_poll_seconds: float = 120.0,
) -> DeployResult:
    """Creates a new SemanticModel item in a Fabric workspace from TMDL
    files, over plain HTTPS -- no desktop application involved.

    Handles the API's long-running-operation pattern: a ``202 Accepted``
    response is polled via its ``Location`` header until the operation
    succeeds or fails (or ``max_poll_seconds`` elapses).
    """
    if not workspace_id.strip():
        return DeployResult(success=False, message="Workspace ID is required.")
    if not bearer_token.strip():
        return DeployResult(success=False, message="Bearer token is required.")

    url = f"{FABRIC_API_BASE}/workspaces/{workspace_id.strip()}/semanticModels"
    body: Dict[str, Any] = {
        "displayName": display_name,
        "definition": {
            "format": "TMDL",
            "parts": build_definition_parts(tmdl_files, pbism_bytes, platform_bytes),
        },
    }
    if description:
        body["description"] = description

    headers = {"Authorization": f"Bearer {bearer_token.strip()}", "Content-Type": "application/json"}

    try:
        resp = requests.post(url, json=body, headers=headers, timeout=timeout)
    except requests.RequestException as e:  # noqa: BLE001
        return DeployResult(success=False, message=f"Network error calling the Fabric API: {e}")

    return _handle_response(resp, headers, workspace_id, poll_interval, max_poll_seconds)


def _handle_response(resp, headers, workspace_id, poll_interval, max_poll_seconds) -> DeployResult:
    if resp.status_code in (200, 201):
        data = _safe_json(resp)
        return DeployResult(
            success=True,
            message="Semantic model created.",
            item_id=(data or {}).get("id"),
            workspace_url=f"https://app.powerbi.com/groups/{workspace_id}/list",
            raw_response=data,
        )

    if resp.status_code == 202:
        op_url = resp.headers.get("Location")
        retry_after = float(resp.headers.get("Retry-After", poll_interval) or poll_interval)
        if not op_url:
            return DeployResult(
                success=False,
                message="Fabric API accepted the request (202) but returned no operation URL to poll.",
            )
        return _poll_operation(op_url, headers, workspace_id, retry_after, max_poll_seconds)

    return DeployResult(
        success=False,
        message=f"Fabric API returned HTTP {resp.status_code}: {_safe_json(resp) or resp.text}",
        raw_response=_safe_json(resp),
    )


def _poll_operation(op_url: str, headers: Dict[str, str], workspace_id: str, retry_after: float, max_poll_seconds: float) -> DeployResult:
    elapsed = 0.0
    while elapsed < max_poll_seconds:
        time.sleep(retry_after)
        elapsed += retry_after
        try:
            resp = requests.get(op_url, headers=headers, timeout=30)
        except requests.RequestException as e:  # noqa: BLE001
            return DeployResult(success=False, message=f"Network error while polling deployment status: {e}")

        if resp.status_code not in (200, 202):
            return DeployResult(
                success=False,
                message=f"Polling failed with HTTP {resp.status_code}: {_safe_json(resp) or resp.text}",
                raw_response=_safe_json(resp),
            )

        data = _safe_json(resp) or {}
        status = data.get("status")
        if status == "Succeeded":
            item_id = _fetch_operation_result_id(resp, headers)
            return DeployResult(
                success=True,
                message="Semantic model created successfully.",
                item_id=item_id,
                workspace_url=f"https://app.powerbi.com/groups/{workspace_id}/list",
                raw_response=data,
            )
        if status == "Failed":
            return DeployResult(success=False, message=f"Fabric deployment failed: {data.get('error')}", raw_response=data)

        retry_after = float(resp.headers.get("Retry-After", retry_after) or retry_after)

    return DeployResult(success=False, message="Timed out waiting for the Fabric deployment to complete.")


def _fetch_operation_result_id(resp, headers: Dict[str, str]) -> Optional[str]:
    result_url = resp.headers.get("Location")
    if not result_url:
        return None
    try:
        r2 = requests.get(result_url, headers=headers, timeout=30)
        if r2.status_code == 200:
            return (_safe_json(r2) or {}).get("id")
    except requests.RequestException:
        return None
    return None


def _safe_json(resp) -> Optional[Dict[str, Any]]:
    try:
        return resp.json()
    except ValueError:
        return None
