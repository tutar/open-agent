"""Unified OpenAgent host CLI."""

from __future__ import annotations

import argparse

from openagent.host import OpenAgentHost, OpenAgentHostConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openagent-host")
    parser.add_argument(
        "--channel",
        action="append",
        default=[],
        help="Preload a channel at startup. May be repeated.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    host = OpenAgentHost(OpenAgentHostConfig.from_env(preload_channels=args.channel))
    host.start()


if __name__ == "__main__":
    main()
