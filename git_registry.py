"""Store and retrieve Ossie semantic model YAML files in a GitHub-backed
"model registry" repository, via the GitHub Contents API -- no local git
clone, no desktop application, just plain HTTPS calls.

Reference: https://docs.github.com/en/rest/repos/contents

Layout in the registry repo:

    basemodel/<model_name>.ossie.yaml   -- saved from the Base Model section
    AIEnrich/<model_name>.ossie.yaml    -- saved from the Enrich Base Model section

Each model name maps to a single file that gets **overwritten** on every
save ("last write wins" -- whichever save reaches GitHub last simply
replaces the file's content, no conflict is surfaced to the user).
Versioning comes from git's own commit history on that file
(``git log basemodel/<model_name>.ossie.yaml`` in the registry repo, or the
repo's commit history on GitHub), rather than accumulating separate
timestamped files.
"""

from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

GITHUB_API_BASE = "https://api.github.com"

# Overridable via .env / environment variables (see .env.example); these
# fall back to this project's own registry repo.
DEFAULT_OWNER = os.environ.get("GIT_REGISTRY_OWNER", "MaheshK812210")
DEFAULT_REPO = os.environ.get("GIT_REGISTRY_REPO", "ModelRegitry")
DEFAULT_BRANCH = os.environ.get("GIT_REGISTRY_BRANCH", "main")

BASE_MODEL_DIR = "basemodel"
AI_ENRICH_DIR = "AIEnrich"

MODEL_FILE_SUFFIX = ".ossie.yaml"


@dataclass
class RegistryResult:
    success: bool
    message: str
    content: Optional[str] = None
    files: Optional[List[str]] = None
    commit_url: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None


def get_registry_token() -> Optional[str]:
    """Reads the registry token from the environment (populated from
    ``.env`` via ``python-dotenv`` at app startup, or a real environment
    variable / Cloud Secret in a deployed setting). Returns ``None`` if
    unset."""
    return os.environ.get("GIT_REGISTRY_TOKEN") or None


def safe_model_filename(model_name: str) -> str:
    """Sanitizes a model name into a stable file name within a registry
    directory, e.g. ``"account position model"`` ->
    ``"account_position_model.ossie.yaml"``."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", (model_name or "").strip()) or "semantic_model"
    if safe.endswith(MODEL_FILE_SUFFIX):
        return safe
    return f"{safe}{MODEL_FILE_SUFFIX}"


def display_name_from_filename(filename: str) -> str:
    if filename.endswith(MODEL_FILE_SUFFIX):
        return filename[: -len(MODEL_FILE_SUFFIX)]
    return filename


def _headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _contents_url(directory: str, filename: Optional[str], owner: str, repo: str) -> str:
    path = directory if filename is None else f"{directory}/{filename}"
    return f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}"


def _safe_json(resp) -> Optional[Dict[str, Any]]:
    try:
        return resp.json()
    except ValueError:
        return None


def list_models(
    directory: str,
    token: str,
    owner: str = DEFAULT_OWNER,
    repo: str = DEFAULT_REPO,
    branch: str = DEFAULT_BRANCH,
) -> RegistryResult:
    """Lists the ``*.ossie.yaml`` files currently saved under ``directory``
    in the registry repo."""
    if not token:
        return RegistryResult(success=False, message="No registry token configured.")

    url = _contents_url(directory, None, owner, repo)
    try:
        resp = requests.get(url, headers=_headers(token), params={"ref": branch}, timeout=20)
    except requests.RequestException as e:  # noqa: BLE001
        return RegistryResult(success=False, message=f"Network error listing models: {e}")

    if resp.status_code == 404:
        # The directory doesn't exist yet (no model has ever been saved there).
        return RegistryResult(success=True, message="No models saved yet.", files=[])
    if resp.status_code != 200:
        return RegistryResult(
            success=False, message=f"GitHub API returned HTTP {resp.status_code}: {_safe_json(resp) or resp.text}"
        )

    items = _safe_json(resp) or []
    if isinstance(items, dict):
        # A single file matched the path instead of a directory listing.
        items = [items]
    files = sorted(item["name"] for item in items if item.get("type") == "file" and item["name"].endswith(MODEL_FILE_SUFFIX))
    return RegistryResult(success=True, message=f"Found {len(files)} model(s).", files=files)


def load_model(
    directory: str,
    filename: str,
    token: str,
    owner: str = DEFAULT_OWNER,
    repo: str = DEFAULT_REPO,
    branch: str = DEFAULT_BRANCH,
) -> RegistryResult:
    """Fetches and decodes a saved model's YAML text."""
    if not token:
        return RegistryResult(success=False, message="No registry token configured.")

    url = _contents_url(directory, filename, owner, repo)
    try:
        resp = requests.get(url, headers=_headers(token), params={"ref": branch}, timeout=20)
    except requests.RequestException as e:  # noqa: BLE001
        return RegistryResult(success=False, message=f"Network error loading model: {e}")

    if resp.status_code != 200:
        return RegistryResult(
            success=False, message=f"GitHub API returned HTTP {resp.status_code}: {_safe_json(resp) or resp.text}"
        )

    data = _safe_json(resp) or {}
    try:
        text = base64.b64decode(data.get("content", "")).decode("utf-8")
    except Exception as e:  # noqa: BLE001
        return RegistryResult(success=False, message=f"Failed to decode file content: {e}")

    return RegistryResult(success=True, message="Loaded.", content=text, raw_response=data)


def save_model(
    directory: str,
    filename: str,
    yaml_text: str,
    commit_message: str,
    token: str,
    owner: str = DEFAULT_OWNER,
    repo: str = DEFAULT_REPO,
    branch: str = DEFAULT_BRANCH,
) -> RegistryResult:
    """Creates or overwrites ``directory/filename`` in the registry repo
    with ``yaml_text``, committing with ``commit_message``.

    Always re-fetches the file's current ``sha`` immediately before writing
    (GitHub's API requires it to update an existing file) -- this makes the
    save "last write wins": whichever call reaches GitHub last simply wins,
    with no conflict surfaced to the caller.
    """
    if not token:
        return RegistryResult(success=False, message="No registry token configured.")

    url = _contents_url(directory, filename, owner, repo)

    sha = None
    try:
        existing = requests.get(url, headers=_headers(token), params={"ref": branch}, timeout=20)
        if existing.status_code == 200:
            sha = (_safe_json(existing) or {}).get("sha")
    except requests.RequestException as e:  # noqa: BLE001
        return RegistryResult(success=False, message=f"Network error checking for an existing file: {e}")

    body: Dict[str, Any] = {
        "message": commit_message.strip() if commit_message and commit_message.strip() else f"Save {filename}",
        "content": base64.b64encode(yaml_text.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha

    try:
        resp = requests.put(url, json=body, headers=_headers(token), timeout=30)
    except requests.RequestException as e:  # noqa: BLE001
        return RegistryResult(success=False, message=f"Network error saving model: {e}")

    if resp.status_code not in (200, 201):
        return RegistryResult(
            success=False, message=f"GitHub API returned HTTP {resp.status_code}: {_safe_json(resp) or resp.text}"
        )

    data = _safe_json(resp) or {}
    commit_url = (data.get("commit") or {}).get("html_url")
    return RegistryResult(success=True, message="Saved to the registry.", commit_url=commit_url, raw_response=data)
