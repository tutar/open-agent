"""CLI wrapper for preloading the Feishu channel on the unified host."""

from __future__ import annotations

from openagent.cli.host import main as _host_main


def main() -> None:
    """Start the unified host with the Feishu channel preloaded."""

    _host_main(["--channel", "feishu"])


if __name__ == "__main__":
    main()
