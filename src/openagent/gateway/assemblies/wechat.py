"""Compatibility exports for WeChat assembly helpers."""

from openagent.gateway.channels.wechat.assembly import (
    WechatAppConfig,
    create_wechat_gateway,
    create_wechat_host,
    create_wechat_host_from_env,
    create_wechat_runtime,
)

__all__ = [
    "WechatAppConfig",
    "create_wechat_gateway",
    "create_wechat_host",
    "create_wechat_host_from_env",
    "create_wechat_runtime",
]
