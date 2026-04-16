"""Channel-specific gateway integrations."""

from .feishu import FeishuBotClient, FeishuChannelAdapter
from .local import DesktopChannelAdapter, TerminalChannelAdapter

__all__ = [
    "DesktopChannelAdapter",
    "FeishuBotClient",
    "FeishuChannelAdapter",
    "TerminalChannelAdapter",
]
