from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import pytest


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _render_command(template: str, **kwargs: str) -> list[str]:
    quoted_kwargs = {key: shlex.quote(value) for key, value in kwargs.items()}
    return shlex.split(template.format(**quoted_kwargs))


def _skip_if_e2e_disabled() -> None:
    if os.getenv("OPENAGENT_RUN_FEISHU_E2E") != "1":
        pytest.skip("Set OPENAGENT_RUN_FEISHU_E2E=1 to run real Feishu E2E tests")


def _skip_if_missing_cli() -> None:
    binary = os.getenv("OPENAGENT_FEISHU_E2E_LARK_CLI_BIN", "lark-cli")
    if shutil.which(binary) is None:
        pytest.skip(f"{binary} is not installed")
    status = subprocess.run(
        [binary, "auth", "status"],
        capture_output=True,
        text=True,
        check=False,
    )
    if status.returncode != 0:
        pytest.skip(f"{binary} auth status failed; run '{binary} auth login --recommend'")


def _ensure_environment() -> None:
    _skip_if_e2e_disabled()
    _skip_if_missing_cli()
    _require_env("OPENAGENT_FEISHU_APP_ID")
    _require_env("OPENAGENT_FEISHU_APP_SECRET")
    _require_env("OPENAGENT_FEISHU_E2E_P2P_CHAT_ID")
    _require_env("OPENAGENT_MODEL")
    _require_env("OPENAGENT_BASE_URL")


def _skip_if_group_e2e_disabled() -> None:
    if os.getenv("OPENAGENT_RUN_FEISHU_GROUP_E2E") != "1":
        pytest.skip("Set OPENAGENT_RUN_FEISHU_GROUP_E2E=1 to run real Feishu group E2E tests")


def _ensure_group_environment() -> None:
    _ensure_environment()
    _skip_if_group_e2e_disabled()
    _require_env("OPENAGENT_FEISHU_E2E_GROUP_ID")


def _send_private_until_log(
    host: HostProcess,
    driver: LarkCliDriver,
    text: str,
    needle: str,
    *,
    attempts: int = 2,
    timeout: float = 30,
) -> int:
    """Send a private message and retry once if the expected host log does not arrive."""

    last_error: AssertionError | None = None
    for _ in range(attempts):
        offset = host.snapshot()
        driver.send_private(text)
        try:
            host.wait_for(needle, after=offset, timeout=timeout)
            return offset
        except AssertionError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _wait_for_any_log(
    host: HostProcess,
    needles: list[str],
    *,
    after: int = 0,
    timeout: float = 20,
) -> str:
    last_error: AssertionError | None = None
    for needle in needles:
        try:
            return host.wait_for(needle, after=after, timeout=timeout)
        except AssertionError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


@dataclass(slots=True)
class HostProcess:
    process: subprocess.Popen[str]
    lines: deque[str]
    _thread: threading.Thread

    @classmethod
    def start(cls, workdir: Path, session_root: Path) -> HostProcess:
        env = os.environ.copy()
        env.setdefault("OPENAGENT_FEISHU_GROUP_AT_ONLY", "true")
        env["OPENAGENT_SESSION_ROOT"] = str(session_root / "sessions")
        env["OPENAGENT_BINDING_ROOT"] = str(session_root / "sessions")
        process = subprocess.Popen(
            [sys.executable, "-m", "tests.e2e.support.feishu_e2e_host"],
            cwd=workdir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        lines: deque[str] = deque(maxlen=2000)

        def _reader() -> None:
            for line in process.stdout:
                lines.append(line.rstrip())

        thread = threading.Thread(target=_reader, daemon=True)
        thread.start()
        host = cls(process=process, lines=lines, _thread=thread)
        host.wait_for("feishu-host> starting long connection", timeout=20)
        # The Feishu client reports websocket readiness asynchronously after startup.
        host.wait_for("connected to wss://", timeout=20)
        return host

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)

    def snapshot(self) -> int:
        return len(self.lines)

    def wait_for(self, needle: str, *, after: int = 0, timeout: float = 20) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            current = list(self.lines)
            for line in current[after:]:
                if needle in line:
                    return line
            if self.process.poll() is not None:
                joined = "\n".join(current)
                raise AssertionError(
                    f"Host exited before log appeared: {needle}\nCaptured logs:\n{joined}"
                )
            time.sleep(0.1)
        joined = "\n".join(self.lines)
        raise AssertionError(f"Timed out waiting for log: {needle}\nCaptured logs:\n{joined}")

    def assert_absent(self, needle: str, *, after: int = 0, duration: float = 3.0) -> None:
        deadline = time.time() + duration
        while time.time() < deadline:
            current = list(self.lines)
            if any(needle in line for line in current[after:]):
                joined = "\n".join(current)
                raise AssertionError(f"Unexpected log found: {needle}\nCaptured logs:\n{joined}")
            if self.process.poll() is not None:
                return
            time.sleep(0.1)


@dataclass(slots=True)
class LarkCliDriver:
    binary: str
    p2p_chat_id: str
    group_chat_id: str | None
    bot_name: str
    p2p_template: str
    group_template: str
    group_mention_template: str

    @classmethod
    def from_env(cls) -> LarkCliDriver:
        return cls(
            binary=os.getenv("OPENAGENT_FEISHU_E2E_LARK_CLI_BIN", "lark-cli"),
            p2p_chat_id=_require_env("OPENAGENT_FEISHU_E2E_P2P_CHAT_ID"),
            group_chat_id=os.getenv("OPENAGENT_FEISHU_E2E_GROUP_ID"),
            bot_name=os.getenv("OPENAGENT_FEISHU_E2E_BOT_NAME", "openagent"),
            p2p_template=os.getenv(
                "OPENAGENT_FEISHU_E2E_P2P_SEND_TEMPLATE",
                "{binary} im +messages-send --as user --chat-id {chat_id} --text {text}",
            ),
            group_template=os.getenv(
                "OPENAGENT_FEISHU_E2E_GROUP_SEND_TEMPLATE",
                "{binary} im +messages-send --as user --chat-id {chat_id} --text {text}",
            ),
            group_mention_template=os.getenv(
                "OPENAGENT_FEISHU_E2E_GROUP_MENTION_TEMPLATE",
                "{binary} im +messages-send --as user --chat-id {chat_id} "
                "--text {mention_text}",
            ),
        )

    def _run(self, template: str, *, chat_id: str, text: str) -> subprocess.CompletedProcess[str]:
        command = _render_command(
            template,
            binary=self.binary,
            chat_id=chat_id,
            text=text,
            bot_name=self.bot_name,
            mention_text=f"@{self.bot_name} {text}",
        )
        return subprocess.run(command, capture_output=True, text=True, check=True)

    def send_private(self, text: str) -> None:
        self._run(self.p2p_template, chat_id=self.p2p_chat_id, text=text)

    def send_group_plain(self, text: str) -> None:
        if self.group_chat_id is None:
            raise RuntimeError("OPENAGENT_FEISHU_E2E_GROUP_ID is required for group E2E")
        self._run(self.group_template, chat_id=self.group_chat_id, text=text)

    def send_group_mention(self, text: str) -> None:
        if self.group_chat_id is None:
            raise RuntimeError("OPENAGENT_FEISHU_E2E_GROUP_ID is required for group E2E")
        self._run(self.group_mention_template, chat_id=self.group_chat_id, text=text)


@pytest.fixture()
def feishu_e2e_environment(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[HostProcess, LarkCliDriver]:
    _ensure_environment()
    workdir = Path(__file__).resolve().parents[2]
    session_root = tmp_path_factory.mktemp("feishu-e2e-runtime")
    host = HostProcess.start(workdir=workdir, session_root=session_root)
    driver = LarkCliDriver.from_env()
    try:
        yield host, driver
    finally:
        host.stop()


@pytest.mark.skip(
    reason="Real Feishu p2p chats do not reliably deliver an initial slash command; "
    "this case remains covered by deterministic unit tests."
)
def test_feishu_e2e_missing_session_control(
    feishu_e2e_environment: tuple[HostProcess, LarkCliDriver],
) -> None:
    host, driver = feishu_e2e_environment
    offset = host.snapshot()
    driver.send_private("/approve")

    host.wait_for("feishu-host> no bound session for control input; sending hint", after=offset)
    host.wait_for(
        "No active session is bound for this chat yet. Send a normal message first.",
        after=offset,
    )


@pytest.mark.feishu_e2e
def test_feishu_e2e_private_reply_card_lifecycle(
    feishu_e2e_environment: tuple[HostProcess, LarkCliDriver],
) -> None:
    host, driver = feishu_e2e_environment
    offset = host.snapshot()
    driver.send_private("hello")

    host.wait_for("feishu-host> received raw event", after=offset)
    host.wait_for("normalized input kind=user_message", after=offset)
    host.wait_for("sending reply card", after=offset)
    host.wait_for("resolving card id", after=offset)
    _wait_for_any_log(
        host,
        [
            "agent stream_update_card",
            "cardkit streaming unavailable; falling back to message patch",
            "agent patch_card",
        ],
        after=offset,
    )
    _wait_for_any_log(
        host,
        ["status=completed", "status=requires_action"],
        after=offset,
        timeout=40,
    )
    host.assert_absent("status=failed", after=offset, duration=1.5)


@pytest.mark.feishu_e2e
def test_feishu_e2e_private_workspace_listing_does_not_fail(
    feishu_e2e_environment: tuple[HostProcess, LarkCliDriver],
) -> None:
    host, driver = feishu_e2e_environment
    offset = host.snapshot()
    driver.send_private("当前目录下有哪些文件")

    host.wait_for("feishu-host> received raw event", after=offset)
    host.wait_for("normalized input kind=user_message", after=offset)
    host.wait_for("sending reply card", after=offset)
    host.wait_for("resolving card id", after=offset)
    _wait_for_any_log(
        host,
        [
            "agent stream_update_card",
            "cardkit streaming unavailable; falling back to message patch",
            "agent patch_card",
        ],
        after=offset,
    )
    _wait_for_any_log(
        host,
        ["status=requires_action", "status=completed"],
        after=offset,
        timeout=60,
    )
    host.assert_absent("status=failed", after=offset, duration=1.5)
    host.assert_absent("HTTP 500", after=offset, duration=1.5)
    host.assert_absent("System message must be at the beginning", after=offset, duration=1.5)
    host.assert_absent(
        "Bash: [Errno 2] No such file or directory: '$PWD'",
        after=offset,
        duration=1.5,
    )


@pytest.mark.feishu_e2e
def test_feishu_e2e_private_firecrawl_search_does_not_fail(
    feishu_e2e_environment: tuple[HostProcess, LarkCliDriver],
) -> None:
    host, driver = feishu_e2e_environment
    offset = host.snapshot()
    driver.send_private("搜一下最新hermes-agent 新闻")

    host.wait_for("feishu-host> received raw event", after=offset)
    host.wait_for("normalized input kind=user_message", after=offset)
    host.wait_for("sending reply card", after=offset)
    host.wait_for("resolving card id", after=offset)
    _wait_for_any_log(
        host,
        [
            "agent stream_update_card",
            "cardkit streaming unavailable; falling back to message patch",
            "agent patch_card",
        ],
        after=offset,
    )
    host.wait_for("status=completed", after=offset, timeout=90)
    host.assert_absent("status=failed", after=offset, duration=1.5)
    host.assert_absent("HTTP 500", after=offset, duration=1.5)
    host.assert_absent("HTTP 502", after=offset, duration=1.5)
    host.assert_absent("Turn failed", after=offset, duration=1.5)


@pytest.mark.feishu_e2e
def test_feishu_e2e_private_intro_does_not_fail_with_bare_502(
    feishu_e2e_environment: tuple[HostProcess, LarkCliDriver],
) -> None:
    host, driver = feishu_e2e_environment
    offset = host.snapshot()
    driver.send_private("介绍下自己")

    host.wait_for("feishu-host> received raw event", after=offset)
    host.wait_for("normalized input kind=user_message", after=offset)
    host.wait_for("sending reply card", after=offset)
    host.wait_for("resolving card id", after=offset)
    _wait_for_any_log(
        host,
        [
            "agent stream_update_card",
            "cardkit streaming unavailable; falling back to message patch",
            "agent patch_card",
        ],
        after=offset,
    )
    _wait_for_any_log(
        host,
        ["status=completed", "status=requires_action"],
        after=offset,
        timeout=60,
    )
    host.assert_absent("status=failed", after=offset, duration=1.5)
    host.assert_absent("Turn failed: HTTP 502:", after=offset, duration=1.5)
    host.assert_absent(
        "HTTP 502: upstream returned an empty error body",
        after=offset,
        duration=1.5,
    )


@pytest.mark.feishu_e2e
def test_feishu_e2e_private_approval_and_progress(
    feishu_e2e_environment: tuple[HostProcess, LarkCliDriver],
) -> None:
    host, driver = feishu_e2e_environment
    approval_offset = _send_private_until_log(
        host,
        driver,
        "admin rotate",
        "status=requires_action",
    )
    host.wait_for("status=running", after=approval_offset)
    host.wait_for("status=requires_action", after=approval_offset)


@pytest.mark.feishu_group_e2e
def test_feishu_e2e_group_mention_and_plain_message(
    feishu_e2e_environment: tuple[HostProcess, LarkCliDriver],
) -> None:
    _ensure_group_environment()
    host, driver = feishu_e2e_environment
    mention_offset = host.snapshot()
    driver.send_group_mention("group hello")

    host.wait_for("normalized input kind=user_message", after=mention_offset)
    host.wait_for(
        f"conversation=feishu:chat:{driver.group_chat_id}",
        after=mention_offset,
    )
    host.wait_for("sending reply card", after=mention_offset)
    host.wait_for("resolving card id", after=mention_offset)
    _wait_for_any_log(
        host,
        [
            "agent stream_update_card",
            "cardkit streaming unavailable; falling back to message patch",
            "agent patch_card",
        ],
        after=mention_offset,
    )
    host.wait_for("status=running", after=mention_offset)
    host.wait_for("status=completed", after=mention_offset, timeout=40)
    host.assert_absent("@_user_", after=mention_offset)

    plain_offset = host.snapshot()
    driver.send_group_plain("plain group hello")
    host.wait_for("feishu-host> ignored event after normalization", after=plain_offset)
    host.assert_absent("normalized input kind=user_message", after=plain_offset)
