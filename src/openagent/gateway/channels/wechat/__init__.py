"""WeChat private-chat channel integration."""

from .adapter import WechatChannelAdapter, WechatRawEvent
from .assembly import (
    WechatAppConfig,
    create_wechat_gateway,
    create_wechat_host,
    create_wechat_host_from_env,
    create_wechat_runtime,
)
from .client import WechatBotClient, WechatSdkClient
from .dedupe import FileWechatInboundDedupeStore, InMemoryWechatInboundDedupeStore
from .host import WechatPrivateChatHost

__all__ = [
    "FileWechatInboundDedupeStore",
    "InMemoryWechatInboundDedupeStore",
    "WechatAppConfig",
    "WechatBotClient",
    "WechatChannelAdapter",
    "WechatPrivateChatHost",
    "WechatRawEvent",
    "WechatSdkClient",
    "create_wechat_gateway",
    "create_wechat_host",
    "create_wechat_host_from_env",
    "create_wechat_runtime",
]
