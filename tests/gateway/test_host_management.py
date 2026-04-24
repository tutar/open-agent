from pathlib import Path
from typing import cast

import pytest

from openagent.harness.providers import ProviderConfigurationError
from openagent.harness.runtime.io import (
    ModelProviderExchange,
    ModelTurnRequest,
    ModelTurnResponse,
)
from openagent.host import OpenAgentHost, OpenAgentHostConfig
from openagent.object_model import JsonValue


class StubHostModel:
    provider_family = "test"

    def generate(self, request: ModelTurnRequest) -> ModelTurnResponse:
        return self.generate_with_exchange(request).response

    def generate_with_exchange(self, request: ModelTurnRequest) -> ModelProviderExchange:
        del request
        return ModelProviderExchange(response=ModelTurnResponse(assistant_message="ok"))


def build_host(tmp_path: Path) -> OpenAgentHost:
    agent_root = tmp_path / "agent_default"
    return OpenAgentHost(
        OpenAgentHostConfig(
            openagent_root=str(tmp_path),
            agent_root=str(agent_root),
            session_root=str(agent_root / "sessions"),
            binding_root=str(agent_root / "bindings"),
            data_root=str(agent_root / "data"),
            model_io_root=str(agent_root / "model-io"),
            terminal_host="127.0.0.1",
            terminal_port=8765,
        ),
        model=StubHostModel(),
    )


def test_channel_command_lists_loaded_available_and_usage(tmp_path: Path) -> None:
    host = build_host(tmp_path)

    responses = host.handle_management_command("/channel")

    assert len(responses) == 1
    response = responses[0]
    assert response["type"] == "status"
    assert response["loaded"] == []
    assert response["available"] == ["terminal", "feishu", "wechat", "wecom"]
    usage = cast(list[JsonValue], response["usage"])
    assert "/channel-config feishu app_id <value>" in usage
    assert "/channel-config wechat allowed_senders <comma-separated>" in usage
    assert "/channel-config wecom allowed_users <comma-separated>" in usage


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
    monkeypatch.setattr(
        host._channel_manager,
        "ensure_channel_loaded",
        lambda channel: host._channel_manager.loaded_channels.add(channel),
    )

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
    monkeypatch.setattr(
        host._channel_manager,
        "ensure_channel_loaded",
        lambda channel: host._channel_manager.loaded_channels.add(channel),
    )

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


def test_channel_wecom_reports_missing_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAGENT_WECOM_BOT_ID", raising=False)
    monkeypatch.delenv("OPENAGENT_WECOM_SECRET", raising=False)
    host = build_host(tmp_path)

    responses = host.handle_management_command("/channel wecom")

    assert len(responses) == 1
    response = responses[0]
    assert response["type"] == "error"
    assert response["missing_fields"] == ["bot_id", "secret"]


def test_channel_config_and_load_wecom_are_process_local(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = build_host(tmp_path)
    monkeypatch.setattr(
        host._channel_manager,
        "ensure_channel_loaded",
        lambda channel: host._channel_manager.loaded_channels.add(channel),
    )

    store_bot = host.handle_management_command("/channel-config wecom bot_id bot_1")
    store_secret = host.handle_management_command("/channel-config wecom secret secret_1")
    store_ws_url = host.handle_management_command(
        "/channel-config wecom ws_url wss://example.test"
    )
    store_allowed = host.handle_management_command(
        "/channel-config wecom allowed_users userid_1,userid_2"
    )
    loaded = host.handle_management_command("/channel wecom")

    assert store_bot[0]["type"] == "status"
    assert store_secret[0]["type"] == "status"
    assert store_ws_url[0]["type"] == "status"
    assert store_allowed[0]["type"] == "status"
    assert loaded[0]["type"] == "status"
    assert loaded[0]["message"] == "wecom channel loaded"
    loaded_channels = cast(list[JsonValue], host.describe_channels()["loaded"])
    assert "wecom" in loaded_channels


def test_host_config_from_env_expands_openagent_root_references(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENAGENT_ROOT", str(tmp_path / ".openagent"))

    config = OpenAgentHostConfig.from_env()

    expected_root = (tmp_path / ".openagent").resolve()
    expected_host_root = (expected_root / "agent_default").resolve()
    assert Path(config.openagent_root) == expected_root
    assert Path(config.agent_root) == expected_host_root
    assert Path(config.session_root) == (expected_host_root / "sessions").resolve()
    assert Path(config.binding_root) == (expected_host_root / "bindings").resolve()
    assert Path(config.data_root) == (expected_host_root / "data").resolve()
    assert Path(config.model_io_root) == (expected_host_root / "model-io").resolve()


def test_host_config_from_env_ignores_legacy_root_overrides(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENAGENT_ROOT", str(tmp_path / ".openagent"))
    monkeypatch.setenv("OPENAGENT_HOST_ROOT", "/tmp/legacy-host-root")
    monkeypatch.setenv("OPENAGENT_SESSION_ROOT", "/tmp/legacy-sessions")
    monkeypatch.setenv("OPENAGENT_BINDING_ROOT", "/tmp/legacy-bindings")
    monkeypatch.setenv("OPENAGENT_DATA_ROOT", "/tmp/legacy-data")
    monkeypatch.setenv("OPENAGENT_MODEL_IO_ROOT", "/tmp/legacy-model-io")

    config = OpenAgentHostConfig.from_env()

    expected_agent_root = (tmp_path / ".openagent" / "agent_default").resolve()
    assert Path(config.agent_root) == expected_agent_root
    assert Path(config.session_root) == expected_agent_root / "sessions"
    assert Path(config.binding_root) == expected_agent_root / "bindings"
    assert Path(config.data_root) == expected_agent_root / "data"
    assert Path(config.model_io_root) == expected_agent_root / "model-io"


def test_host_config_direct_init_derives_paths_from_openagent_root(tmp_path: Path) -> None:
    config = OpenAgentHostConfig(openagent_root=str(tmp_path / ".state"))

    expected_root = (tmp_path / ".state").resolve()
    expected_agent_root = expected_root / "agent_default"

    assert Path(config.openagent_root) == expected_root
    assert Path(config.agent_root) == expected_agent_root
    assert Path(config.session_root) == expected_agent_root / "sessions"
    assert Path(config.binding_root) == expected_agent_root / "bindings"
    assert Path(config.data_root) == expected_agent_root / "data"
    assert Path(config.model_io_root) == expected_agent_root / "model-io"


def test_host_config_direct_init_preserves_explicit_path_overrides(tmp_path: Path) -> None:
    custom_agent_root = tmp_path / "custom-agent"
    config = OpenAgentHostConfig(
        openagent_root=str(tmp_path / ".state"),
        agent_root=str(custom_agent_root),
        session_root=str(custom_agent_root / "custom-sessions"),
        binding_root=str(custom_agent_root / "custom-bindings"),
        data_root=str(custom_agent_root / "custom-data"),
        model_io_root=str(custom_agent_root / "custom-model-io"),
    )

    assert Path(config.agent_root) == custom_agent_root
    assert Path(config.session_root) == custom_agent_root / "custom-sessions"
    assert Path(config.binding_root) == custom_agent_root / "custom-bindings"
    assert Path(config.data_root) == custom_agent_root / "custom-data"
    assert Path(config.model_io_root) == custom_agent_root / "custom-model-io"


def test_host_requires_model_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENAGENT_MODEL", raising=False)
    monkeypatch.delenv("OPENAGENT_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAGENT_PROVIDER", raising=False)

    with pytest.raises(ProviderConfigurationError) as exc:
        OpenAgentHost(
            OpenAgentHostConfig(
                openagent_root=str(tmp_path),
                agent_root=str(tmp_path / "agent_default"),
                session_root=str(tmp_path / "agent_default" / "sessions"),
                binding_root=str(tmp_path / "agent_default" / "bindings"),
            )
        )

    assert "OPENAGENT_MODEL is required" in str(exc.value)


def test_host_channel_manager_uses_agent_root_for_feishu_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAGENT_FEISHU_APP_ID", "app")
    monkeypatch.setenv("OPENAGENT_FEISHU_APP_SECRET", "secret")
    host = build_host(tmp_path)

    config = host._channel_manager._resolve_feishu_config()  # type: ignore[attr-defined]

    assert Path(config.session_root) == (tmp_path / "agent_default" / "sessions").resolve()
    assert Path(config.binding_root) == (tmp_path / "agent_default" / "bindings").resolve()
    assert Path(config.card_state_root) == (
        tmp_path / "agent_default" / "cards" / "feishu"
    ).resolve()
