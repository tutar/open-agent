import json
import os
import socket
import sys
from pathlib import Path
from subprocess import PIPE, Popen
from time import sleep


def _allocate_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _start_host(tmp_path: Path) -> tuple[Popen[str], int]:
    terminal_port = _allocate_port()
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["OPENAGENT_TERMINAL_HOST"] = "127.0.0.1"
    env["OPENAGENT_TERMINAL_PORT"] = str(terminal_port)
    env["OPENAGENT_SESSION_ROOT"] = str(tmp_path / "sessions")
    env["OPENAGENT_BINDING_ROOT"] = str(tmp_path / "bindings")
    env["PYTHONPATH"] = str(repo_root / "src")
    process = Popen(
        [sys.executable, "-m", "openagent.cli.host"],
        stdin=PIPE,
        stdout=PIPE,
        stderr=PIPE,
        text=True,
        env=env,
        cwd=str(repo_root),
    )
    assert process.stdout is not None
    for _ in range(50):
        line = process.stdout.readline().strip()
        if "openagent-host> ready" in line:
            return process, terminal_port
        sleep(0.05)
    process.kill()
    raise AssertionError("Host did not become ready")


def _connect_terminal_client(port: int) -> tuple[socket.socket, object, object]:
    client = socket.create_connection(("127.0.0.1", port), timeout=3)
    reader = client.makefile("r", encoding="utf-8")
    writer = client.makefile("w", encoding="utf-8")
    return client, reader, writer


def test_terminal_client_smoke(tmp_path: Path) -> None:
    host, terminal_port = _start_host(tmp_path)
    client, reader, writer = _connect_terminal_client(terminal_port)

    ready = json.loads(reader.readline())
    assert ready["message"] == "ready"

    writer.write(json.dumps({"kind": "message", "content": "hello"}) + "\n")
    writer.flush()

    event_types = [json.loads(reader.readline())["event_type"] for _ in range(3)]

    reader.close()
    writer.close()
    client.close()
    host.kill()

    assert event_types == ["turn_started", "assistant_message", "turn_completed"]


def test_terminal_client_session_binding_and_listing(tmp_path: Path) -> None:
    host, terminal_port = _start_host(tmp_path)
    client, reader, writer = _connect_terminal_client(terminal_port)

    ready = json.loads(reader.readline())
    assert ready["message"] == "ready"
    assert ready["session_name"] == "main"

    writer.write(json.dumps({"kind": "bind", "session_name": "ops"}) + "\n")
    writer.flush()

    bound = json.loads(reader.readline())
    assert bound["message"] == "bound"
    assert bound["session_name"] == "ops"

    writer.write(json.dumps({"kind": "list_sessions"}) + "\n")
    writer.flush()

    listing = json.loads(reader.readline())

    reader.close()
    writer.close()
    client.close()
    host.kill()

    assert listing["type"] == "sessions"
    assert listing["current_session_name"] == "ops"
    assert listing["sessions"] == ["main", "ops"]


def test_terminal_client_management_command_lists_channels(tmp_path: Path) -> None:
    host, terminal_port = _start_host(tmp_path)
    client, reader, writer = _connect_terminal_client(terminal_port)

    ready = json.loads(reader.readline())
    assert ready["message"] == "ready"

    writer.write(json.dumps({"kind": "management", "command": "/channel"}) + "\n")
    writer.flush()

    response = json.loads(reader.readline())

    reader.close()
    writer.close()
    client.close()
    host.kill()

    assert response["type"] == "status"
    assert response["available"] == ["terminal", "feishu"]
