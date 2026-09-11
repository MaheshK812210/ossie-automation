import base64
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fabric_deploy as fd


def _response(status_code, json_data=None, headers=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("no json")
    return resp


def test_build_definition_parts_base64_encodes_everything():
    tmdl_files = {"definition/model.tmdl": "model X\n"}
    parts = fd.build_definition_parts(tmdl_files, b'{"version": "4.2"}', b'{"metadata": {}}')
    paths = {p["path"] for p in parts}
    assert paths == {"definition.pbism", "definition/model.tmdl", ".platform"}
    for p in parts:
        assert p["payloadType"] == "InlineBase64"
        decoded = base64.b64decode(p["payload"])
        assert len(decoded) > 0


def test_build_definition_parts_without_platform():
    parts = fd.build_definition_parts({}, b"{}")
    assert {p["path"] for p in parts} == {"definition.pbism"}


def test_create_semantic_model_requires_workspace_and_token():
    r = fd.create_semantic_model("", "token", "name", {}, b"{}")
    assert r.success is False
    assert "Workspace ID" in r.message

    r = fd.create_semantic_model("ws-id", "", "name", {}, b"{}")
    assert r.success is False
    assert "Bearer token" in r.message


@patch("fabric_deploy.requests.post")
def test_create_semantic_model_synchronous_success(mock_post):
    mock_post.return_value = _response(201, json_data={"id": "item-123", "displayName": "MyModel"})
    result = fd.create_semantic_model("ws-1", "tok", "MyModel", {"definition/model.tmdl": "model X"}, b"{}")
    assert result.success is True
    assert result.item_id == "item-123"
    assert result.workspace_url == "https://app.powerbi.com/groups/ws-1/list"

    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["headers"]["Authorization"] == "Bearer tok"
    assert call_kwargs["json"]["displayName"] == "MyModel"
    assert call_kwargs["json"]["definition"]["format"] == "TMDL"


@patch("fabric_deploy.requests.post")
def test_create_semantic_model_error_response(mock_post):
    mock_post.return_value = _response(400, json_data={"error": {"message": "bad request"}})
    result = fd.create_semantic_model("ws-1", "tok", "MyModel", {}, b"{}")
    assert result.success is False
    assert "400" in result.message


@patch("fabric_deploy.requests.post")
def test_create_semantic_model_network_error(mock_post):
    import requests

    mock_post.side_effect = requests.ConnectionError("boom")
    result = fd.create_semantic_model("ws-1", "tok", "MyModel", {}, b"{}")
    assert result.success is False
    assert "Network error" in result.message


@patch("fabric_deploy.time.sleep", return_value=None)
@patch("fabric_deploy.requests.get")
@patch("fabric_deploy.requests.post")
def test_create_semantic_model_long_running_operation_success(mock_post, mock_get, mock_sleep):
    mock_post.return_value = _response(
        202, headers={"Location": "https://api.fabric.microsoft.com/v1/operations/op-1", "Retry-After": "1"}
    )
    mock_get.side_effect = [
        _response(200, json_data={"status": "Running"}, headers={"Retry-After": "1"}),
        _response(200, json_data={"status": "Succeeded"}, headers={"Location": "https://api.fabric.microsoft.com/v1/operations/op-1/result"}),
        _response(200, json_data={"id": "item-999"}),
    ]
    result = fd.create_semantic_model("ws-1", "tok", "MyModel", {}, b"{}", poll_interval=0.01)
    assert result.success is True
    assert result.item_id == "item-999"


@patch("fabric_deploy.time.sleep", return_value=None)
@patch("fabric_deploy.requests.get")
@patch("fabric_deploy.requests.post")
def test_create_semantic_model_long_running_operation_failure(mock_post, mock_get, mock_sleep):
    mock_post.return_value = _response(202, headers={"Location": "https://api.fabric.microsoft.com/v1/operations/op-2"})
    mock_get.return_value = _response(200, json_data={"status": "Failed", "error": {"message": "nope"}})
    result = fd.create_semantic_model("ws-1", "tok", "MyModel", {}, b"{}", poll_interval=0.01)
    assert result.success is False
    assert "Fabric deployment failed" in result.message


@patch("fabric_deploy.requests.post")
def test_create_semantic_model_202_without_location_header(mock_post):
    mock_post.return_value = _response(202, headers={})
    result = fd.create_semantic_model("ws-1", "tok", "MyModel", {}, b"{}")
    assert result.success is False
    assert "operation URL" in result.message
