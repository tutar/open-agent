from __future__ import annotations

import os
import socket
import sys
import threading
from contextlib import suppress


def emit_error(message: str) -> None:
    print(f'{{"type":"error","message":"{message}"}}', flush=True)


def resolve_terminal_endpoint() -> tuple[str, int]:
    host = os.getenv("OPENAGENT_TERMINAL_HOST", "127.0.0.1")
    port = int(os.getenv("OPENAGENT_TERMINAL_PORT", "8765"))
    return host, port


def main() -> None:
    host, port = resolve_terminal_endpoint()
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.connect((host, port))
    except OSError:
        emit_error(
            "host_unavailable:start python -m openagent.cli.host "
            f"(terminal={host}:{port})"
        )
        raise SystemExit(1)

    reader = client.makefile("r", encoding="utf-8")
    writer = client.makefile("w", encoding="utf-8")

    def pump_stdout() -> None:
        for line in reader:
            print(line.rstrip("\n"), flush=True)

    thread = threading.Thread(target=pump_stdout, name="openagent-bridge-reader", daemon=True)
    thread.start()

    try:
        for raw in sys.stdin:
            writer.write(raw)
            writer.flush()
    finally:
        with suppress(OSError):
            client.shutdown(socket.SHUT_RDWR)
        reader.close()
        writer.close()
        client.close()


if __name__ == "__main__":
    main()
