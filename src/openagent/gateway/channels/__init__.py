"""Channel-specific gateway integrations."""

from .feishu import FeishuBotClient, FeishuChannelAdapter
from .local import TerminalChannelAdapter

__all__ = [
    "FeishuBotClient",
    "FeishuChannelAdapter",
    "TerminalChannelAdapter",
]
