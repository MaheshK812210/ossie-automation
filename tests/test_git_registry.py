import base64
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import git_registry as gr


def _response(status_code, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("no json")
    return resp


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("account_position_model", "account_position_model.ossie.yaml"),
        ("account position model", "account_position_model.ossie.yaml"),
        ("My Model! v1.0", "My_Model__v1.0.ossie.yaml"),
        ("", "semantic_model.ossie.yaml"),
        ("already.ossie.yaml", "already.ossie.yaml"),
    ],
)
def test_safe_model_filename(raw, expected):
    assert gr.safe_model_filename(raw) == expected


def test_display_name_from_filename():
    assert gr.display_name_from_filename("account_position_model.ossie.yaml") == "account_position_model"
    assert gr.display_name_from_filename("plain.yaml") == "plain.yaml"


def test_get_registry_token_reads_env(monkeypatch):
    monkeypatch.delenv("GIT_REGISTRY_TOKEN", raising=False)
    assert gr.get_registry_token() is None
    monkeypatch.setenv("GIT_REGISTRY_TOKEN", "tok123")
    assert gr.get_registry_token() == "tok123"


def test_list_models_requires_token():
    result = gr.list_models("basemodel", "")
    assert result.success is False
    assert "token" in result.message.lower()


@patch("git_registry.requests.get")
def test_list_models_success(mock_get):
    mock_get.return_value = _response(
        200,
        json_data=[
            {"name": "a.ossie.yaml", "type": "file"},
            {"name": "b.ossie.yaml", "type": "file"},
            {"name": "not_yaml.txt", "type": "file"},
            {"name": "subdir", "type": "dir"},
        ],
    )
    result = gr.list_models("basemodel", "tok")
    assert result.success is True
    assert result.files == ["a.ossie.yaml", "b.ossie.yaml"]


@patch("git_registry.requests.get")
def test_list_models_missing_directory_returns_empty(mock_get):
    mock_get.return_value = _response(404)
    result = gr.list_models("basemodel", "tok")
    assert result.success is True
    assert result.files == []


@patch("git_registry.requests.get")
def test_list_models_error_response(mock_get):
    mock_get.return_value = _response(403, json_data={"message": "Bad credentials"})
    result = gr.list_models("basemodel", "tok")
    assert result.success is False
    assert "403" in result.message


@patch("git_registry.requests.get")
def test_list_models_network_error(mock_get):
    import requests

    mock_get.side_effect = requests.ConnectionError("boom")
    result = gr.list_models("basemodel", "tok")
    assert result.success is False
    assert "Network error" in result.message


@patch("git_registry.requests.get")
def test_load_model_success(mock_get):
    content = "version: 0.2.0.dev0\nsemantic_model: []\n"
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    mock_get.return_value = _response(200, json_data={"content": encoded, "sha": "abc123"})
    result = gr.load_model("basemodel", "m.ossie.yaml", "tok")
    assert result.success is True
    assert result.content == content


@patch("git_registry.requests.get")
def test_load_model_not_found(mock_get):
    mock_get.return_value = _response(404, json_data={"message": "Not Found"})
    result = gr.load_model("basemodel", "missing.ossie.yaml", "tok")
    assert result.success is False
    assert "404" in result.message


def test_load_model_requires_token():
    result = gr.load_model("basemodel", "m.ossie.yaml", "")
    assert result.success is False


@patch("git_registry.requests.put")
@patch("git_registry.requests.get")
def test_save_model_creates_new_file_without_sha(mock_get, mock_put):
    mock_get.return_value = _response(404)  # no existing file
    mock_put.return_value = _response(
        201, json_data={"commit": {"html_url": "https://github.com/x/y/commit/123"}}
    )
    result = gr.save_model("basemodel", "m.ossie.yaml", "content", "my message", "tok")
    assert result.success is True
    assert result.commit_url == "https://github.com/x/y/commit/123"

    put_body = mock_put.call_args.kwargs["json"]
    assert "sha" not in put_body
    assert put_body["message"] == "my message"
    assert base64.b64decode(put_body["content"]).decode("utf-8") == "content"


@patch("git_registry.requests.put")
@patch("git_registry.requests.get")
def test_save_model_overwrites_existing_file_with_sha(mock_get, mock_put):
    mock_get.return_value = _response(200, json_data={"sha": "existing-sha"})
    mock_put.return_value = _response(200, json_data={"commit": {"html_url": "https://github.com/x/y/commit/456"}})
    result = gr.save_model("basemodel", "m.ossie.yaml", "new content", "", "tok")
    assert result.success is True

    put_body = mock_put.call_args.kwargs["json"]
    assert put_body["sha"] == "existing-sha"
    # Blank commit message falls back to a default.
    assert "m.ossie.yaml" in put_body["message"]


@patch("git_registry.requests.put")
@patch("git_registry.requests.get")
def test_save_model_error_response(mock_get, mock_put):
    mock_get.return_value = _response(404)
    mock_put.return_value = _response(422, json_data={"message": "Validation failed"})
    result = gr.save_model("basemodel", "m.ossie.yaml", "content", "msg", "tok")
    assert result.success is False
    assert "422" in result.message


def test_save_model_requires_token():
    result = gr.save_model("basemodel", "m.ossie.yaml", "content", "msg", "")
    assert result.success is False


@patch("git_registry.requests.get")
def test_save_model_network_error_on_sha_check(mock_get):
    import requests

    mock_get.side_effect = requests.ConnectionError("boom")
    result = gr.save_model("basemodel", "m.ossie.yaml", "content", "msg", "tok")
    assert result.success is False
    assert "Network error" in result.message
