"""Shared package utilities and constants."""

from openagent.shared.paths import normalize_workspace_root
from openagent.shared.version import SPEC_VERSION, __version__

__all__ = ["SPEC_VERSION", "__version__", "normalize_workspace_root"]
