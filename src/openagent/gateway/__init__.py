"""Gateway package exports."""

from .assemblies.feishu import (
    FeishuAppConfig,
    create_feishu_gateway,
    create_feishu_host,
    create_feishu_host_from_env,
    create_feishu_runtime,
)
from .binding_store import FileSessionBindingStore
from .channels import (
    FeishuBotClient,
    FeishuChannelAdapter,
    TerminalChannelAdapter,
)
from .core import Gateway
from .hosts.feishu import (
    FEISHU_REACTION_COMPLETED,
    FEISHU_REACTION_IN_PROGRESS,
    FeishuHostRunLock,
    FeishuLongConnectionHost,
    OfficialFeishuBotClient,
)
from .hosts.feishu_dedupe import (
    FileFeishuInboundDedupeStore,
    InMemoryFeishuInboundDedupeStore,
)
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
