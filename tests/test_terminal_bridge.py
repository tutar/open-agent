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


def _start_bridge(tmp_path: Path, terminal_port: int) -> Popen[str]:
    bridge = (
        Path(__file__).resolve().parents[1] / "frontend" / "terminal-tui" / "scripts" / "bridge.py"
    )
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["OPENAGENT_TERMINAL_HOST"] = "127.0.0.1"
    env["OPENAGENT_TERMINAL_PORT"] = str(terminal_port)
    env["PYTHONPATH"] = str(repo_root / "src")
    process = Popen(
        [sys.executable, str(bridge)],
        stdin=PIPE,
        stdout=PIPE,
        stderr=PIPE,
        text=True,
        env=env,
    )
    return process


def test_terminal_bridge_smoke(tmp_path: Path) -> None:
    host, terminal_port = _start_host(tmp_path)
    process = _start_bridge(tmp_path, terminal_port)

    assert process.stdout is not None
    assert process.stdin is not None

    ready = json.loads(process.stdout.readline())
    assert ready["message"] == "ready"

    process.stdin.write(json.dumps({"kind": "message", "content": "hello"}) + "\n")
    process.stdin.flush()

    event_types = [json.loads(process.stdout.readline())["event_type"] for _ in range(3)]
    process.kill()
    host.kill()

    assert event_types == ["turn_started", "assistant_message", "turn_completed"]


def test_terminal_bridge_session_binding_and_listing(tmp_path: Path) -> None:
    host, terminal_port = _start_host(tmp_path)
    process = _start_bridge(tmp_path, terminal_port)

    assert process.stdin is not None
    assert process.stdout is not None

    ready = json.loads(process.stdout.readline())
    assert ready["message"] == "ready"
    assert ready["session_name"] == "main"

    process.stdin.write(json.dumps({"kind": "bind", "session_name": "ops"}) + "\n")
    process.stdin.flush()

    bound = json.loads(process.stdout.readline())
    assert bound["message"] == "bound"
    assert bound["session_name"] == "ops"

    process.stdin.write(json.dumps({"kind": "list_sessions"}) + "\n")
    process.stdin.flush()

    listing = json.loads(process.stdout.readline())
    process.kill()
    host.kill()

    assert listing["type"] == "sessions"
    assert listing["current_session_name"] == "ops"
    assert listing["sessions"] == ["main", "ops"]


def test_terminal_bridge_management_command_lists_channels(tmp_path: Path) -> None:
    host, terminal_port = _start_host(tmp_path)
    process = _start_bridge(tmp_path, terminal_port)

    assert process.stdin is not None
    assert process.stdout is not None

    ready = json.loads(process.stdout.readline())
    assert ready["message"] == "ready"

    process.stdin.write(json.dumps({"kind": "management", "command": "/channel"}) + "\n")
    process.stdin.flush()

    response = json.loads(process.stdout.readline())
    process.kill()
    host.kill()

    assert response["type"] == "status"
    assert response["available"] == ["terminal", "feishu"]
