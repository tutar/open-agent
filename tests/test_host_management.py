from pathlib import Path

from openagent.host import OpenAgentHost, OpenAgentHostConfig


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
    assert response["available"] == ["terminal", "feishu"]
    assert "/channel-config feishu app_id <value>" in response["usage"]


def test_channel_feishu_reports_missing_config(tmp_path: Path) -> None:
    host = build_host(tmp_path)

    responses = host.handle_management_command("/channel feishu")

    assert len(responses) == 1
    response = responses[0]
    assert response["type"] == "error"
    assert response["missing_fields"] == ["app_id", "app_secret"]


def test_channel_config_and_load_feishu_are_process_local(
    tmp_path: Path,
    monkeypatch,
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
    assert "feishu" in host.describe_channels()["loaded"]
