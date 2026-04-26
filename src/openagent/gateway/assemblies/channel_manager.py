"""Gateway-oriented channel startup and management helpers."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from openagent.gateway.channels import (
    FeishuChannelAdapter,
    WechatChannelAdapter,
    WeComChannelAdapter,
)
from openagent.gateway.channels.feishu import FeishuAppConfig, create_feishu_host
from openagent.gateway.channels.wechat import WechatAppConfig, create_wechat_host
from openagent.gateway.channels.wecom import WeComAppConfig, create_wecom_host
from openagent.gateway.core import Gateway
from openagent.gateway.interfaces import ChannelAdapter
from openagent.host.config import OpenAgentHostConfig
from openagent.object_model import JsonObject, JsonValue
from openagent.shared import resolve_cards_root, resolve_path_env


@dataclass(frozen=True, slots=True)
class _ChannelSpec:
    supported_config_keys: tuple[str, ...]
    usage: tuple[str, ...]


CHANNEL_SPECS: dict[str, _ChannelSpec] = {
    "feishu": _ChannelSpec(
        supported_config_keys=("app_id", "app_secret"),
        usage=(
            "/channel-config feishu app_id <value>",
            "/channel-config feishu app_secret <value>",
        ),
    ),
    "wechat": _ChannelSpec(
        supported_config_keys=("base_url", "cred_path", "allowed_senders"),
        usage=(
            "/channel-config wechat base_url <value>",
            "/channel-config wechat cred_path <value>",
            "/channel-config wechat allowed_senders <comma-separated>",
        ),
    ),
    "wecom": _ChannelSpec(
        supported_config_keys=("bot_id", "secret", "ws_url", "allowed_users"),
        usage=(
            "/channel-config wecom bot_id <value>",
            "/channel-config wecom secret <value>",
            "/channel-config wecom ws_url <value>",
            "/channel-config wecom allowed_users <comma-separated>",
        ),
    ),
}


class ChannelHostManager:
    """Own channel-specific config resolution and host startup outside Gateway core."""

    def __init__(
        self,
        gateway: Gateway,
        host_config: OpenAgentHostConfig,
        *,
        management_handler: Callable[[str], list[JsonObject]] | None = None,
    ) -> None:
        self._gateway = gateway
        self._host_config = host_config
        self._management_handler = management_handler
        self._runtime_config: dict[str, dict[str, str]] = {}
        self._loaded_channels: set[str] = set()
        self._channel_threads: list[threading.Thread] = []

    @property
    def available_channels(self) -> tuple[str, ...]:
        return tuple(CHANNEL_SPECS)

    @property
    def loaded_channels(self) -> set[str]:
        return self._loaded_channels

    @property
    def usage_lines(self) -> list[str]:
        lines = ["/channel", "/channel <name>"]
        for spec in CHANNEL_SPECS.values():
            lines.extend(spec.usage)
        return lines

    def set_channel_config(self, channel_name: str, key: str, value: str) -> JsonObject:
        channel = channel_name.strip().lower()
        spec = CHANNEL_SPECS.get(channel)
        if spec is None:
            return {"type": "error", "message": f"unsupported channel config target: {channel}"}
        if key not in spec.supported_config_keys:
            return {"type": "error", "message": f"unsupported {channel} config key: {key}"}
        self._runtime_config.setdefault(channel, {})[key] = value
        return {"type": "status", "message": f"stored runtime config for {channel}.{key}"}

    def ensure_channel_loaded(self, channel: str) -> None:
        normalized = channel.strip().lower()
        if normalized in self._loaded_channels:
            return
        if normalized == "feishu":
            config_feishu = self._resolve_feishu_config()
            self._start_channel_host(
                channel_type="feishu",
                adapter_factory=lambda: FeishuChannelAdapter(
                    mention_required_in_group=config_feishu.mention_required_in_group
                ),
                thread_name="openagent-feishu-host",
                target_factory=lambda: create_feishu_host(
                    self._gateway,
                    config_feishu,
                    management_handler=self._management_handler,
                ).run,
            )
        elif normalized == "wechat":
            config_wechat = self._resolve_wechat_config()
            self._start_channel_host(
                channel_type="wechat",
                adapter_factory=WechatChannelAdapter,
                thread_name="openagent-wechat-host",
                target_factory=lambda: create_wechat_host(
                    self._gateway,
                    config_wechat,
                    management_handler=self._management_handler,
                ).run,
            )
        elif normalized == "wecom":
            config_wecom = self._resolve_wecom_config()
            self._start_channel_host(
                channel_type="wecom",
                adapter_factory=WeComChannelAdapter,
                thread_name="openagent-wecom-host",
                target_factory=lambda: create_wecom_host(
                    self._gateway,
                    config_wecom,
                    management_handler=self._management_handler,
                ).run,
            )
        else:
            raise ValueError(f"Unsupported channel: {normalized}")
        self._loaded_channels.add(normalized)

    def load_channel_from_command(self, channel_name: str) -> JsonObject:
        channel = channel_name.strip().lower()
        if channel not in CHANNEL_SPECS:
            return {"type": "error", "message": f"unknown channel: {channel}"}
        if channel in self._loaded_channels:
            return {"type": "status", "message": f"{channel} channel is already loaded"}
        missing = self._missing_config_fields(channel)
        if missing:
            spec = CHANNEL_SPECS[channel]
            return {
                "type": "error",
                "message": (
                    f"{channel} config missing: {', '.join(missing)}"
                    f" | use {' and '.join(spec.usage)}"
                ),
                "missing_fields": cast(JsonValue, missing),
            }
        self.ensure_channel_loaded(channel)
        return {"type": "status", "message": f"{channel} channel loaded"}

    def _start_channel_host(
        self,
        *,
        channel_type: str,
        adapter_factory: Callable[[], ChannelAdapter],
        thread_name: str,
        target_factory: Callable[[], Callable[[], None]],
    ) -> None:
        try:
            self._gateway.get_channel_adapter(channel_type)
        except KeyError:
            self._gateway.register_channel(adapter_factory())
        target = target_factory()
        thread = threading.Thread(target=target, name=thread_name, daemon=True)
        thread.start()
        self._channel_threads.append(thread)

    def _runtime_value(self, channel: str, key: str, env_name: str, default: str = "") -> str:
        runtime = self._runtime_config.get(channel, {})
        value = runtime.get(key)
        if value is not None:
            return value
        return os.getenv(env_name, default)

    def _missing_config_fields(self, channel: str) -> list[str]:
        missing: list[str] = []
        if channel == "feishu":
            if not self._runtime_value("feishu", "app_id", "OPENAGENT_FEISHU_APP_ID"):
                missing.append("app_id")
            if not self._runtime_value("feishu", "app_secret", "OPENAGENT_FEISHU_APP_SECRET"):
                missing.append("app_secret")
        elif channel == "wecom":
            if not self._runtime_value("wecom", "bot_id", "OPENAGENT_WECOM_BOT_ID"):
                missing.append("bot_id")
            if not self._runtime_value("wecom", "secret", "OPENAGENT_WECOM_SECRET"):
                missing.append("secret")
        return missing

    def _resolve_feishu_config(self) -> FeishuAppConfig:
        app_id = self._runtime_value("feishu", "app_id", "OPENAGENT_FEISHU_APP_ID")
        app_secret = self._runtime_value("feishu", "app_secret", "OPENAGENT_FEISHU_APP_SECRET")
        if not app_id:
            raise RuntimeError("OPENAGENT_FEISHU_APP_ID is required")
        if not app_secret:
            raise RuntimeError("OPENAGENT_FEISHU_APP_SECRET is required")
        return FeishuAppConfig(
            app_id=app_id,
            app_secret=app_secret,
            openagent_root=self._host_config.openagent_root,
            agent_root=self._host_config.agent_root,
            session_root=self._host_config.session_root,
            binding_root=self._host_config.binding_root,
            lock_root=resolve_path_env(
                "OPENAGENT_FEISHU_LOCK_ROOT",
                str(Path("/tmp") / "openagent-feishu-locks"),
            )
            or str(Path("/tmp") / "openagent-feishu-locks"),
            mention_required_in_group=os.getenv("OPENAGENT_FEISHU_GROUP_AT_ONLY", "true").lower()
            != "false",
            card_state_root=resolve_path_env(
                "OPENAGENT_FEISHU_CARD_STATE_ROOT",
                resolve_cards_root(self._host_config.openagent_root, "feishu"),
            )
            or resolve_cards_root(self._host_config.openagent_root, "feishu"),
        )

    def _resolve_wechat_config(self) -> WechatAppConfig:
        allowed_senders_value = self._runtime_value(
            "wechat",
            "allowed_senders",
            "OPENAGENT_WECHAT_ALLOWED_SENDERS",
        )
        return WechatAppConfig(
            base_url=self._runtime_value(
                "wechat",
                "base_url",
                "OPENAGENT_WECHAT_BASE_URL",
                "https://ilinkai.weixin.qq.com",
            ),
            openagent_root=self._host_config.openagent_root,
            agent_root=self._host_config.agent_root,
            cred_path=self._runtime_value(
                "wechat",
                "cred_path",
                "OPENAGENT_WECHAT_CRED_PATH",
                str(Path(".openagent") / "wechat" / "credentials.json"),
            ),
            session_root=self._host_config.session_root,
            binding_root=self._host_config.binding_root,
            allowed_senders=tuple(
                sender.strip()
                for sender in allowed_senders_value.split(",")
                if sender.strip()
            ),
        )

    def _resolve_wecom_config(self) -> WeComAppConfig:
        bot_id = self._runtime_value("wecom", "bot_id", "OPENAGENT_WECOM_BOT_ID")
        secret = self._runtime_value("wecom", "secret", "OPENAGENT_WECOM_SECRET")
        if not bot_id:
            raise RuntimeError("OPENAGENT_WECOM_BOT_ID is required")
        if not secret:
            raise RuntimeError("OPENAGENT_WECOM_SECRET is required")
        allowed_users_value = self._runtime_value(
            "wecom",
            "allowed_users",
            "OPENAGENT_WECOM_ALLOWED_USERS",
        )
        ws_url = self._runtime_value(
            "wecom",
            "ws_url",
            "OPENAGENT_WECOM_WS_URL",
            "wss://openws.work.weixin.qq.com",
        )
        return WeComAppConfig(
            bot_id=bot_id,
            secret=secret,
            openagent_root=self._host_config.openagent_root,
            agent_root=self._host_config.agent_root,
            ws_url=ws_url,
            ping_interval_seconds=float(
                os.getenv("OPENAGENT_WECOM_PING_INTERVAL_SECONDS", "30")
            ),
            session_root=self._host_config.session_root,
            binding_root=self._host_config.binding_root,
            allowed_users=tuple(
                user.strip() for user in allowed_users_value.split(",") if user.strip()
            ),
        )
