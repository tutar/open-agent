"""Compatibility exports for Feishu assembly helpers."""

from openagent.gateway.channels.feishu.assembly import (
    FeishuAppConfig,
    create_feishu_gateway,
    create_feishu_host,
    create_feishu_host_from_env,
    create_feishu_runtime,
    main,
)

__all__ = [
    "FeishuAppConfig",
    "create_feishu_gateway",
    "create_feishu_host",
    "create_feishu_host_from_env",
    "create_feishu_runtime",
    "main",
]
