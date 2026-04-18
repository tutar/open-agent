"""Gateway package exports."""

from .binding_store import FileSessionBindingStore
from .channels import FeishuChannelAdapter, TerminalChannelAdapter
from .channels.feishu import (
    FEISHU_REACTION_COMPLETED,
    FEISHU_REACTION_IN_PROGRESS,
    FeishuAppConfig,
    FeishuBotClient,
    FeishuHostRunLock,
    FeishuLongConnectionHost,
    FileFeishuInboundDedupeStore,
    InMemoryFeishuInboundDedupeStore,
    OfficialFeishuBotClient,
    create_feishu_gateway,
    create_feishu_host,
    create_feishu_host_from_env,
    create_feishu_runtime,
)
from .core import Gateway
from .interfaces import ChannelAdapter, SessionAdapter, SessionBindingStore
from .models import (
    ChannelIdentity,
    EgressEnvelope,
    InboundEnvelope,
    LocalSessionHandle,
    NormalizedInboundMessage,
    SessionBinding,
)
from .session_adapter import InProcessSessionAdapter

__all__ = [
    "ChannelAdapter",
    "ChannelIdentity",
    "EgressEnvelope",
    "FileSessionBindingStore",
    "FileFeishuInboundDedupeStore",
    "FEISHU_REACTION_COMPLETED",
    "FEISHU_REACTION_IN_PROGRESS",
    "FeishuAppConfig",
    "FeishuBotClient",
    "FeishuChannelAdapter",
    "FeishuHostRunLock",
    "FeishuLongConnectionHost",
    "Gateway",
    "InboundEnvelope",
    "InMemoryFeishuInboundDedupeStore",
    "InProcessSessionAdapter",
    "LocalSessionHandle",
    "NormalizedInboundMessage",
    "OfficialFeishuBotClient",
    "SessionAdapter",
    "SessionBinding",
    "SessionBindingStore",
    "TerminalChannelAdapter",
    "create_feishu_gateway",
    "create_feishu_host",
    "create_feishu_host_from_env",
    "create_feishu_runtime",
]
