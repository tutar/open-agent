from pathlib import Path
from typing import cast

import pytest

from openagent.host import OpenAgentHost, OpenAgentHostConfig
from openagent.object_model import JsonValue


def build_host(tmp_path: Path) -> OpenAgentHost:
    return OpenAgentHost(
        OpenAgentHostConfig(
            session_root=str(tmp_path / "sessions"),
            binding_root=str(tmp_path / "bindings"),
            terminal_host="127.0.0.1",
            terminal_port=8765,
        )
    )


def test_channel_command_lists_loaded_available_and_usage(tmp_path: Path) -> None:
    host = build_host(tmp_path)

    responses = host.handle_management_command("/channel")

    assert len(responses) == 1
    response = responses[0]
    assert response["type"] == "status"
    assert response["loaded"] == []
    assert response["available"] == ["terminal", "feishu", "wechat"]
    usage = cast(list[JsonValue], response["usage"])
    assert "/channel-config feishu app_id <value>" in usage
    assert "/channel-config wechat allowed_senders <comma-separated>" in usage


def test_channel_feishu_reports_missing_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAGENT_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("OPENAGENT_FEISHU_APP_SECRET", raising=False)
    host = build_host(tmp_path)

    responses = host.handle_management_command("/channel feishu")

    assert len(responses) == 1
    response = responses[0]
    assert response["type"] == "error"
    assert response["missing_fields"] == ["app_id", "app_secret"]


def test_channel_config_and_load_feishu_are_process_local(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = build_host(tmp_path)
    monkeypatch.setattr(host, "_load_feishu_channel", lambda: None)

    store_app = host.handle_management_command("/channel-config feishu app_id cli_app")
    store_secret = host.handle_management_command(
        "/channel-config feishu app_secret super-secret"
    )
    loaded = host.handle_management_command("/channel feishu")

    assert store_app[0]["type"] == "status"
    assert store_secret[0]["type"] == "status"
    assert loaded[0]["type"] == "status"
    assert loaded[0]["message"] == "feishu channel loaded"
    loaded_channels = cast(list[JsonValue], host.describe_channels()["loaded"])
    assert "feishu" in loaded_channels


def test_channel_config_and_load_wechat_are_process_local(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = build_host(tmp_path)
    monkeypatch.setattr(host, "_load_wechat_channel", lambda: None)

    store_base_url = host.handle_management_command(
        "/channel-config wechat base_url https://example.test"
    )
    store_cred_path = host.handle_management_command(
        "/channel-config wechat cred_path .openagent/wechat/credentials.json"
    )
    store_allowed = host.handle_management_command(
        "/channel-config wechat allowed_senders wx_user_1,wx_user_2"
    )
    loaded = host.handle_management_command("/channel wechat")

    assert store_base_url[0]["type"] == "status"
    assert store_cred_path[0]["type"] == "status"
    assert store_allowed[0]["type"] == "status"
    assert loaded[0]["type"] == "status"
    assert loaded[0]["message"] == "wechat channel loaded"
    loaded_channels = cast(list[JsonValue], host.describe_channels()["loaded"])
    assert "wechat" in loaded_channels
