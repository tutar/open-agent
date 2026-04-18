"""Feishu channel integration surface."""

from .adapter import FeishuBotClient, FeishuChannelAdapter
from .assembly import (
    FeishuAppConfig,
    create_feishu_gateway,
    create_feishu_host,
    create_feishu_host_from_env,
    create_feishu_runtime,
    main,
)
from .client import OfficialFeishuBotClient
from .dedupe import FileFeishuInboundDedupeStore, InMemoryFeishuInboundDedupeStore
from .host import (
    FEISHU_REACTION_COMPLETED,
    FEISHU_REACTION_IN_PROGRESS,
    FeishuHostRunLock,
    FeishuLongConnectionHost,
)

__all__ = [
    "FileFeishuInboundDedupeStore",
    "FEISHU_REACTION_COMPLETED",
    "FEISHU_REACTION_IN_PROGRESS",
    "FeishuAppConfig",
    "FeishuBotClient",
    "FeishuChannelAdapter",
    "FeishuHostRunLock",
    "FeishuLongConnectionHost",
    "InMemoryFeishuInboundDedupeStore",
    "OfficialFeishuBotClient",
    "create_feishu_gateway",
    "create_feishu_host",
    "create_feishu_host_from_env",
    "create_feishu_runtime",
    "main",
]
