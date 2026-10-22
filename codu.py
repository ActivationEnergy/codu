#!/usr/bin/env python3
"""List and manage locally persisted Codex sessions."""

import argparse
import json
import os
import queue
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import threading
import time
import unicodedata
from collections import deque
from collections.abc import Iterable, Sequence
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

APP_SERVER_SOURCE_KINDS = [
    "cli",
    "vscode",
    "exec",
    "appServer",
    "subAgent",
    "subAgentReview",
    "subAgentCompact",
    "subAgentThreadSpawn",
    "subAgentOther",
    "unknown",
]

INJECTED_USER_PREFIXES = (
    "# AGENTS.md",
    "<environment_context>",
    "<permissions instructions>",
    "<collaboration_mode>",
    "<apps_instructions>",
    "<plugins_instructions>",
    "<skills_instructions>",
)

DEFAULT_OPERATION_TIMEOUT = 30.0
__version__ = "0.3.0"


@dataclass(frozen=True)
class Session:
    """The displayable metadata for one Codex session."""

    updated: datetime
    state: str
    status: str
    session_id: str
    size_bytes: int
    title: str
    cwd: str
    path: Path | None


@dataclass(frozen=True)
class DataEntry:
    """One filesystem entry under CODEX_HOME, without following symlinks."""

    path: Path
    kind: str
    size_bytes: int
    updated: datetime
    description: str
    impact: str
    basis: str
    action: str
    complete: bool


DATA_DESCRIPTIONS = {
    ".tmp": ("可能包含当前插件市场根目录、插件副本和锁文件。", "不能按名称当作废弃临时文件整目录删除。"),
    "AGENTS.md": ("用户级 Agent 指令。", "删除会失去个人指令。"),
    "auth.json": ("文件式登录凭据。", "删除可能需要重新登录。"),
    "config.toml": ("用户级 Codex 配置。", "删除会失去个人设置。"),
    "history.jsonl": ("本地命令或提示历史记录。", "删除会失去历史记录。"),
    "session_index.jsonl": ("会话名称等索引。", "应由 Codex 管理。"),
    "sessions": ("未归档的会话记录。", "请在会话视图通过 Codex 删除。"),
    "archived_sessions": ("已归档的会话记录。", "请在会话视图通过 Codex 删除。"),
    "cache": ("Codex 的本地缓存目录。", "各子目录的清理契约尚未确认。"),
    "plugins": ("插件安装及其缓存。", "删除可能使插件不可用。"),
    "skills": ("用户安装或创建的技能。", "删除会失去技能。"),
    "rules": ("用户级规则。", "删除会失去规则。"),
    "memories": ("Codex 保存的记忆内容。", "删除会失去记忆。"),
    "automations": ("本地自动化配置与状态。", "删除可能使自动化失效。"),
    "attachments": ("会话或工具关联的附件。", "删除可能影响已有会话。"),
    "generated_images": ("生成的图片文件。", "删除会失去这些图片。"),
    "computer-use": ("计算机操作功能的数据和组件。", "删除可能影响该功能。"),
    "shell_snapshots": ("会话使用的 shell 环境快照。", "删除可能影响恢复会话。"),
    "sqlite": ("Codex 的本地数据库目录。", "数据库文件不能逐个删除。"),
}

# Recognized names help explain cache contents, but do not establish a safe
# deletion contract for Codex versions that may use them differently.
KNOWN_CACHE = {
    "bundled_plugin_exclusions": "内置插件排除项缓存",
    "codex_app_directory": "Codex 应用目录缓存",
    "codex_apps_server_info": "Codex 应用服务信息缓存",
    "codex_apps_tools": "Codex 应用工具目录缓存",
    "remote_plugin_catalog": "远程插件目录缓存",
}


def allocated_bytes(info: os.stat_result) -> int:
    """Use allocated blocks where available, falling back to apparent size."""
    blocks = getattr(info, "st_blocks", None)
    return blocks * 512 if blocks is not None else info.st_size


def data_size(path: Path, seen: set[tuple[int, int]]) -> tuple[int, bool]:
    """Measure allocated space, counting hard links once and never following links."""
    try:
        info = path.lstat()
    except OSError:
        return 0, False
    identity = (info.st_dev, info.st_ino)
    if identity in seen:
        return 0, True
    seen.add(identity)
    total = allocated_bytes(info)
    if not stat.S_ISDIR(info.st_mode):
        return total, True
    complete = True
    try:
        with os.scandir(path) as children:
            for child in children:
                size, ok = data_size(Path(child.path), seen)
                total += size
                complete &= ok
    except OSError:
        complete = False
    return total, complete


def describe_data_entry(root: Path, path: Path, kind: str) -> tuple[str, str, str, str]:
    """Return description, impact, evidence level, and permitted action."""
    parts = path.relative_to(root).parts
    name = parts[0]
    if kind == "链接":
        return "符号链接；目标内容不计入本目录。", "不会在 codu 中删除。", "文件类型", "只读"
    if len(parts) == 1 and name in {"sessions", "archived_sessions"}:
        description, impact = DATA_DESCRIPTIONS[name]
        return description, impact, "Codex 会话接口", "会话"
    if len(parts) == 2 and name == "cache" and parts[1] in KNOWN_CACHE:
        return (
            f"{KNOWN_CACHE[parts[1]]}；位于 Codex 缓存目录。",
            "尚未核实该目录的清理契约，当前只允许查看。",
            "路径名称；删除行为未验证",
            "核查",
        )
    if len(parts) == 1 and re.fullmatch(r"thread_history_\d+\.sqlite", name):
        return (
            "会话分页历史库；保存 turn、item 及其索引和 JSONL 投影进度。",
            "直接删除会使历史暂时不可读；部分投影可在恢复会话时重建，但无完整恢复保证。",
            "Codex 会话接口及当前 SQLite 表结构",
            "只读",
        )
    if len(parts) == 1 and re.fullmatch(r"logs_\d+\.sqlite", name):
        return "Codex 的 SQLite 诊断日志库。", "不能逐文件删除。", "当前 SQLite 表结构", "只读"
    if len(parts) == 1 and name in DATA_DESCRIPTIONS:
        description, impact = DATA_DESCRIPTIONS[name]
        basis = (
            "OpenAI 官方配置文档"
            if name in {"auth.json", "config.toml", "history.jsonl"}
            else "文件名及本地目录结构"
        )
        return description, impact, basis, "只读"
    if name in {"sessions", "archived_sessions"}:
        return "会话记录的一部分。", "请在会话视图通过 Codex 删除。", "Codex 会话接口", "只读"
    if path.name.endswith((".sqlite", ".sqlite-wal", ".sqlite-shm", ".db", ".db-wal", ".db-shm")):
        return "疑似 Codex 本地数据库或其日志。", "逐个删除可能损坏数据。", "文件名", "只读"
    if name in DATA_DESCRIPTIONS:
        return (
            f"位于 {name}/；具体文件用途未逐项确认。",
            "删除影响未确认。",
            "父目录用途",
            "只读",
        )
    return "用途未确认。", "删除影响未确认。", "未确认", "只读"


def list_data_entries(root: Path, directory: Path) -> list[DataEntry]:
    """List one CODEX_HOME level with allocated sizes and conservative actions."""
    entries: list[DataEntry] = []
    seen: set[tuple[int, int]] = set()
    children = list(directory.iterdir())
    for path in children:
        try:
            info = path.lstat()
        except OSError:
            continue
        kind = "链接" if stat.S_ISLNK(info.st_mode) else "目录" if stat.S_ISDIR(info.st_mode) else "文件"
        description, impact, basis, action = describe_data_entry(root, path, kind)
        size, complete = data_size(path, seen)
        entries.append(
            DataEntry(
                path=path,
                kind=kind,
                size_bytes=size,
                updated=datetime.fromtimestamp(info.st_mtime, UTC),
                description=description,
                impact=impact,
                basis=basis,
                action=action if complete else "只读",
                complete=complete,
            )
        )
    return entries


def filter_and_sort_data(
    entries: Sequence[DataEntry], query: str, sort_key: str, descending: bool
) -> list[DataEntry]:
    needle = query.casefold().strip()
    values = [entry for entry in entries if needle in entry.path.name.casefold()]
    keys = {
        "name": lambda entry: entry.path.name.casefold(),
        "size": lambda entry: entry.size_bytes,
        "updated": lambda entry: entry.updated,
        "action": lambda entry: entry.action,
    }
    return sorted(values, key=keys[sort_key], reverse=descending)


def sqlite_free_bytes(path: Path) -> int | None:
    """Read free pages from a main SQLite snapshot without changing its WAL."""
    if path.is_symlink():
        return None
    try:
        with closing(sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro&immutable=1", uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
            free_pages = db.execute("PRAGMA freelist_count").fetchone()[0]
            return page_size * free_pages
    except (OSError, sqlite3.Error, TypeError, IndexError):
        return None


def data_advice(entries: Sequence[DataEntry]) -> list[str]:
    """Summarize supported cleanup and measured opportunities at CODEX_HOME."""
    by_name = {entry.path.name: entry for entry in entries}
    sessions = sum(
        by_name[name].size_bytes
        for name in ("sessions", "archived_sessions")
        if name in by_name
    )
    cache = by_name.get("cache")
    plugins = by_name.get("plugins")
    temporary = by_name.get(".tmp")
    free_values = [
        sqlite_free_bytes(entry.path)
        for entry in entries
        if entry.kind == "文件" and entry.path.name.endswith(".sqlite")
    ]
    free = sum(value for value in free_values if value is not None)
    free_label = f"约 {human_size(free)}" if free_values and all(
        value is not None for value in free_values
    ) else "未能完整估算"
    return [
        f"会话 {human_size(sessions)}：进入 sessions/ 按 2 找大文件；用 Codex 删除，归档不省空间。",
        f"数据库空闲页 {free_label}：有压缩潜力；当前只读，不直接删除数据库或 WAL。",
        f"插件 {human_size(plugins.size_bytes if plugins else 0)}、.tmp {human_size(temporary.size_bytes if temporary else 0)}：用 Codex 卸载不用的插件；.tmp 可能正在使用。",
        f"缓存 {human_size(cache.size_bytes if cache else 0)}：未验证各子目录的清理契约，先查看用途。",
    ]


class AppServerError(RuntimeError):
    """Raised when the local Codex app-server cannot serve a request."""


class AppServerClient:
    """Small synchronous client for Codex app-server's JSONL transport."""

    _EOF = object()

    def __init__(self, command: Sequence[str], timeout: float | None = None) -> None:
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            )
        except OSError as error:
            raise AppServerError(str(error)) from error
        self.next_id = 0
        self.timeout = operation_timeout() if timeout is None else timeout
        self.stdout_lines: queue.Queue[str | object] = queue.Queue()
        self.stderr_lines: deque[str] = deque(maxlen=100)
        self.closed = False
        self.stdout_thread = threading.Thread(
            target=self._read_stdout,
            name="codu-app-server-stdout",
            daemon=True,
        )
        self.stderr_thread = threading.Thread(
            target=self._read_stderr,
            name="codu-app-server-stderr",
            daemon=True,
        )
        self.stdout_thread.start()
        self.stderr_thread.start()

    def _read_stdout(self) -> None:
        if self.process.stdout is None:
            self.stdout_lines.put(self._EOF)
            return
        try:
            for line in self.process.stdout:
                self.stdout_lines.put(line)
        finally:
            self.stdout_lines.put(self._EOF)

    def _read_stderr(self) -> None:
        if self.process.stderr is None:
            return
        for line in self.process.stderr:
            self.stderr_lines.append(line.rstrip())

    def __enter__(self) -> Self:
        try:
            self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "codu",
                        "title": "codu",
                        "version": __version__,
                    }
                },
            )
            self.notify("initialized", {})
            return self
        except BaseException:
            self.close(graceful=False)
            raise

    def __exit__(self, exception_type: object, *_: object) -> None:
        self.close(graceful=exception_type is None)

    def close(self, graceful: bool = True) -> None:
        if self.closed:
            return
        self.closed = True
        if self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        if not graceful and self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.stdout_thread.join(timeout=1)
        self.stderr_thread.join(timeout=1)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()

    def send(self, message: dict[str, object]) -> None:
        if self.process.stdin is None:
            raise AppServerError("app-server stdin is unavailable")
        try:
            self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise AppServerError(self.failure_message(str(error))) from error

    def notify(self, method: str, params: dict[str, object]) -> None:
        self.send({"method": method, "params": params})

    def request(self, method: str, params: dict[str, object]) -> dict[str, object]:
        request_id = self.next_id
        self.next_id += 1
        self.send({"method": method, "id": request_id, "params": params})

        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AppServerError(
                    self.failure_message(
                        f"{method}: timed out after {self.timeout:g} seconds"
                    )
                )
            try:
                line = self.stdout_lines.get(timeout=remaining)
            except queue.Empty as error:
                raise AppServerError(
                    self.failure_message(
                        f"{method}: timed out after {self.timeout:g} seconds"
                    )
                ) from error
            if line is self._EOF:
                raise AppServerError(
                    self.failure_message(f"{method}: app-server exited")
                )
            if not isinstance(line, str):
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            error = message.get("error")
            if isinstance(error, dict):
                detail = error.get("message") or json.dumps(error, ensure_ascii=False)
                raise AppServerError(f"{method}: {detail}")
            result = message.get("result")
            if not isinstance(result, dict):
                raise AppServerError(f"{method}: invalid response")
            return result

    def failure_message(self, prefix: str) -> str:
        detail = "\n".join(self.stderr_lines).strip()
        return f"{prefix}: {detail}" if detail else prefix


def operation_timeout() -> float:
    """Return the configured timeout for non-interactive Codex operations."""
    raw = os.environ.get("CODU_TIMEOUT")
    if raw is None:
        return DEFAULT_OPERATION_TIMEOUT
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_OPERATION_TIMEOUT
    return value if value > 0 else DEFAULT_OPERATION_TIMEOUT


def parse_timestamp(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp and return an aware datetime when possible."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def normalize_text(value: str) -> str:
    """Collapse whitespace so a prompt fits on one output row."""
    return re.sub(r"\s+", " ", value).strip()


def canonical_directory(value: str | Path) -> Path:
    """Expand and normalize a directory without requiring it to still exist."""
    return Path(value).expanduser().resolve(strict=False)


def status_name(value: object) -> str:
    """Extract the compact runtime status name from an app-server thread."""
    if not isinstance(value, dict):
        return "unknown"
    status_type = value.get("type")
    if not isinstance(status_type, str):
        return "unknown"
    if status_type != "active":
        return status_type
    flags = value.get("activeFlags")
    if isinstance(flags, list) and flags:
        names = [item for item in flags if isinstance(item, str)]
        if names:
            return f"active:{','.join(names)}"
    return "active"


def session_from_thread(thread: dict[str, object], state: str) -> Session | None:
    """Convert one app-server thread summary into a display row."""
    session_id = thread.get("id")
    cwd = thread.get("cwd")
    updated_at = thread.get("updatedAt")
    if (
        not isinstance(session_id, str)
        or not session_id
        or not isinstance(cwd, str)
        or not isinstance(updated_at, int)
    ):
        return None

    raw_path = thread.get("path")
    path = Path(raw_path) if isinstance(raw_path, str) and raw_path else None
    try:
        size = path.stat().st_size if path is not None else 0
    except OSError:
        size = 0

    name = thread.get("name")
    preview = thread.get("preview")
    title = name if isinstance(name, str) and name.strip() else preview
    if not isinstance(title, str) or not title.strip():
        title = "（无名称或简介）"

    return Session(
        updated=datetime.fromtimestamp(updated_at, UTC),
        state=state,
        status=status_name(thread.get("status")),
        session_id=session_id,
        size_bytes=size,
        title=normalize_text(title),
        cwd=cwd,
        path=path,
    )


def list_app_server_page(
    client: AppServerClient,
    state: str,
    directory: Path | None,
) -> Iterable[Session]:
    """Yield every session from one active or archived app-server listing."""
    cursor: str | None = None
    while True:
        params: dict[str, object] = {
            "archived": state == "archived",
            "cursor": cursor,
            "limit": 200,
            "sortKey": "updated_at",
            "sortDirection": "desc",
            "sourceKinds": APP_SERVER_SOURCE_KINDS,
        }
        if directory is not None:
            params["cwd"] = str(directory)

        result = client.request("thread/list", params)
        data = result.get("data")
        if not isinstance(data, list):
            raise AppServerError("thread/list: response is missing data")
        for value in data:
            if not isinstance(value, dict):
                continue
            session = session_from_thread(value, state)
            if session is not None:
                yield session

        next_cursor = result.get("nextCursor")
        if not isinstance(next_cursor, str) or not next_cursor:
            break
        cursor = next_cursor


def discover_sessions_from_app_server(directory: Path | None = None) -> list[Session]:
    """List sessions through the supported Codex app-server interface."""
    executable = os.environ.get("CODEX_BIN") or shutil.which("codex")
    if not executable:
        raise AppServerError("codex executable was not found")

    errors: list[str] = []
    commands = (
        [executable, "app-server", "proxy"],
        [executable, "app-server"],
    )
    for command in commands:
        rows: dict[str, Session] = {}
        try:
            with AppServerClient(command) as client:
                for state in ("active", "archived"):
                    for session in list_app_server_page(client, state, directory):
                        existing = rows.get(session.session_id)
                        if existing is None or session.updated > existing.updated:
                            rows[session.session_id] = session
            return sorted(rows.values(), key=lambda row: row.updated, reverse=True)
        except AppServerError as error:
            errors.append(str(error))

    raise AppServerError("; ".join(errors))


def is_injected_context(text: str) -> bool:
    """Return whether a user-role message is Codex-injected context, not a prompt."""
    stripped = text.lstrip()
    return (
        not stripped
        or stripped.startswith(INJECTED_USER_PREFIXES)
        or "Side conversation boundary." in stripped
    )


def message_text(payload: dict[str, object]) -> str:
    """Join the input-text blocks in a response message."""
    content = payload.get("content")
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "input_text":
            continue
        text = part.get("text")
        if isinstance(text, str):
            parts.append(text)
    return normalize_text("\n".join(parts))


def read_session(path: Path) -> tuple[dict[str, object] | None, str | None, datetime | None]:
    """Read metadata from a legacy session file for the compatibility fallback."""
    meta: dict[str, object] | None = None
    intro: str | None = None
    latest: datetime | None = None

    try:
        stream = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return None, None, None

    with stream:
        for line in stream:
            try:
                item = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(item, dict):
                continue

            timestamp = parse_timestamp(item.get("timestamp"))
            if timestamp is not None and (latest is None or timestamp > latest):
                latest = timestamp

            payload = item.get("payload")
            if not isinstance(payload, dict):
                continue
            if item.get("type") == "session_meta":
                meta = payload
                timestamp = parse_timestamp(payload.get("timestamp"))
                if timestamp is not None and (latest is None or timestamp > latest):
                    latest = timestamp
                continue
            if (
                intro is None
                and item.get("type") == "response_item"
                and payload.get("type") == "message"
                and payload.get("role") == "user"
            ):
                text = message_text(payload)
                if not is_injected_context(text):
                    intro = text

    return meta, intro, latest


def load_session_index(root: Path) -> dict[str, str]:
    """Load the latest title for each session from session_index.jsonl."""
    titles: dict[str, str] = {}
    path = root / "session_index.jsonl"
    try:
        stream = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return titles

    with stream:
        for line in stream:
            try:
                item = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(item, dict):
                continue
            session_id = item.get("id")
            title = item.get("thread_name")
            if isinstance(session_id, str) and isinstance(title, str):
                normalized = normalize_text(title)
                if normalized:
                    titles[session_id] = normalized
    return titles


def discover_sessions(root: Path, directory: Path | None = None) -> list[Session]:
    """Compatibility fallback for Codex versions without app-server thread/list."""
    titles = load_session_index(root)
    rows: dict[str, Session] = {}

    for relative, state in (("sessions", "active"), ("archived_sessions", "archived")):
        session_dir = root / relative
        if not session_dir.is_dir():
            continue
        try:
            paths: Iterable[Path] = session_dir.rglob("*.jsonl")
            for path in paths:
                meta, intro, updated = read_session(path)
                if meta is None:
                    continue
                session_id = meta.get("id")
                raw_cwd = meta.get("cwd")
                if not isinstance(session_id, str) or not session_id:
                    continue
                if not isinstance(raw_cwd, str) or not raw_cwd:
                    raw_cwd = ""
                if directory is not None and (
                    not raw_cwd or canonical_directory(raw_cwd) != directory
                ):
                    continue

                try:
                    stat = path.stat()
                except OSError:
                    continue
                if updated is None:
                    updated = datetime.fromtimestamp(stat.st_mtime).astimezone()
                candidate = Session(
                    updated=updated,
                    state=state,
                    status="unknown",
                    session_id=session_id,
                    size_bytes=stat.st_size,
                    title=titles.get(session_id) or intro or "（无名称或简介）",
                    cwd=raw_cwd,
                    path=path,
                )
                existing = rows.get(session_id)
                if existing is None or candidate.updated > existing.updated:
                    rows[session_id] = candidate
        except OSError:
            continue

    return sorted(rows.values(), key=lambda row: row.updated, reverse=True)


def human_size(size: int) -> str:
    """Format a byte count using compact binary units."""
    value = float(size)
    for unit in ("B", "K", "M", "G", "T"):
        if value < 1024 or unit == "T":
            return f"{int(value)}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    raise AssertionError("unreachable")


def shorten_home(path: str) -> str:
    """Replace the current user's home prefix with '~' for display."""
    if not path:
        return ""
    home = str(Path.home())
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home) :]
    return path


def truncate(value: str, limit: int) -> str:
    """Truncate a value to a maximum number of characters."""
    return value if len(value) <= limit else value[: max(0, limit - 1)] + "…"


def print_tsv(sessions: Sequence[Session]) -> None:
    """Print a human-readable, shell-friendly tab-separated table."""
    print("STATE\tSTATUS\tUPDATED\tSESSION_ID\tSIZE\tNAME / INTRO\tCWD")
    for session in sessions:
        updated = session.updated.astimezone().strftime("%Y-%m-%d %H:%M")
        print(
            f"{session.state}\t{session.status}\t{updated}\t{session.session_id}\t"
            f"{human_size(session.size_bytes)}\t{truncate(session.title, 72)}\t"
            f"{shorten_home(session.cwd)}"
        )


def print_json(sessions: Sequence[Session]) -> None:
    """Print complete session records as JSON."""
    values = []
    for session in sessions:
        value = asdict(session)
        value["updated"] = session.updated.astimezone().isoformat()
        value["path"] = str(session.path) if session.path is not None else None
        values.append(value)
    json.dump(values, sys.stdout, ensure_ascii=False, indent=2)
    print()


def display_width(value: str) -> int:
    """Return an approximate terminal-cell width without external dependencies."""
    width = 0
    for char in value:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


def fit_text(value: str, width: int) -> str:
    """Fit text into terminal cells, appending an ellipsis when truncated."""
    if width <= 0:
        return ""
    if display_width(value) <= width:
        return value + " " * (width - display_width(value))
    if width == 1:
        return "…"

    result: list[str] = []
    used = 0
    for char in value:
        char_width = display_width(char)
        if used + char_width > width - 1:
            break
        result.append(char)
        used += char_width
    return "".join(result) + "…" + " " * max(0, width - used - 1)


def filter_and_sort_sessions(
    sessions: Sequence[Session],
    query: str,
    sort_key: str,
    descending: bool = True,
) -> list[Session]:
    """Filter sessions by visible text and apply a browser sort order."""
    needle = query.casefold().strip()
    values = [
        session
        for session in sessions
        if not needle
        or needle
        in (
            f"{session.session_id} {session.title} {session.cwd} "
            f"{session.state} {session.status}"
        ).casefold()
    ]
    key_functions = {
        "state": lambda item: (item.state.casefold(), item.status.casefold()),
        "size": lambda item: item.size_bytes,
        "updated": lambda item: item.updated,
        "title": lambda item: item.title.casefold(),
        "cwd": lambda item: item.cwd.casefold(),
    }
    key_function = key_functions.get(sort_key, key_functions["updated"])
    return sorted(values, key=key_function, reverse=descending)


def confirmation_choice(key: int) -> str:
    """Map a confirmation keystroke to no, yes, or yes-to-all."""
    if key in (ord("y"), ord("Y")):
        return "yes"
    if key in (ord("a"), ord("A")):
        return "all"
    return "no"


def codex_executable() -> str | None:
    """Resolve the Codex CLI used for session operations."""
    return os.environ.get("CODEX_BIN") or shutil.which("codex")


def run_codex_action(arguments: Sequence[str]) -> tuple[bool, str]:
    """Run a non-interactive Codex session operation and capture its result."""
    executable = codex_executable()
    if not executable:
        return False, "找不到 codex 命令"
    timeout = operation_timeout()
    try:
        result = subprocess.run(
            [executable, *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"Codex 操作在 {timeout:g} 秒后超时"
    except OSError as error:
        return False, str(error)
    output = (result.stderr or result.stdout).strip()
    if result.returncode == 0:
        return True, output
    return False, output or f"codex 返回状态码 {result.returncode}"


class SessionBrowser:
    """An ncdu-style curses browser for Codex sessions."""

    SORT_KEYS = ("state", "size", "updated", "title", "cwd")
    DATA_SORT_KEYS = ("name", "size", "updated", "action")

    def __init__(
        self, screen: object, sessions: Sequence[Session], root: Path | None = None
    ) -> None:
        self.screen = screen
        self.sessions = list(sessions)
        self.root = root or Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
        self.view = "sessions"
        self.query = ""
        self.sort_key = "updated"
        self.sort_descending = True
        self.selected = 0
        self.offset = 0
        self.marked: set[str] = set()
        self.mode = "list"
        self.message = ""
        self.delete_confirm_all = False
        self.data_directory = self.root
        self.data_entries: list[DataEntry] = []
        self.data_query = ""
        self.data_sort_key = "size"
        self.data_descending = True
        self.data_selected = 0
        self.data_offset = 0
        self.data_mode = "list"
        self.data_error = ""
        self.data_advice_lines: list[str] = []

    @property
    def visible(self) -> list[Session]:
        return filter_and_sort_sessions(
            self.sessions, self.query, self.sort_key, self.sort_descending
        )

    def current(self) -> Session | None:
        values = self.visible
        if not values:
            return None
        self.selected = min(self.selected, len(values) - 1)
        return values[self.selected]

    @property
    def data_visible(self) -> list[DataEntry]:
        return filter_and_sort_data(
            self.data_entries, self.data_query, self.data_sort_key, self.data_descending
        )

    def data_current(self) -> DataEntry | None:
        values = self.data_visible
        if not values:
            return None
        self.data_selected = min(self.data_selected, len(values) - 1)
        return values[self.data_selected]

    def refresh_data(self) -> None:
        try:
            self.data_entries = list_data_entries(self.root, self.data_directory)
            self.data_error = ""
            if self.data_directory == self.root:
                self.data_advice_lines = data_advice(self.data_entries)
        except OSError as error:
            self.data_entries = []
            self.data_error = f"无法读取 {shorten_home(str(self.data_directory))}: {error}"
        self.data_selected = min(self.data_selected, max(0, len(self.data_visible) - 1))

    def switch_view(self) -> None:
        if self.view == "sessions":
            self.view = "data"
            self.mode = "list"
            self.data_mode = "list"
            self.refresh_data()
        else:
            self.view = "sessions"
            self.data_mode = "list"
            self.mode = "list"

    def select_session(self, session_id: str) -> None:
        """Keep the same session selected after an operation reorders rows."""
        for index, session in enumerate(self.visible):
            if session.session_id == session_id:
                self.selected = index
                return

    def safe_addstr(self, row: int, column: int, value: str, attribute: int = 0) -> None:
        import curses

        height, width = self.screen.getmaxyx()
        if row < 0 or row >= height or column >= width:
            return
        try:
            self.screen.addstr(row, column, fit_text(value, width - column), attribute)
        except curses.error:
            pass

    def draw_navigation(self) -> None:
        """Keep view selection separate from the changing path and sort summary."""
        import curses

        _, width = self.screen.getmaxyx()
        self.safe_addstr(0, 0, " " * width, curses.A_REVERSE)
        self.safe_addstr(0, 2, "codu", curses.A_BOLD | curses.A_REVERSE)
        self.safe_addstr(0, 10, "│", curses.A_REVERSE)
        session_label = "[会话]" if self.view == "sessions" else "会话"
        data_label = "[Codex 数据]" if self.view == "data" else "Codex 数据"
        self.safe_addstr(0, 14, session_label, curses.A_BOLD | curses.A_REVERSE)
        self.safe_addstr(0, 28, data_label, curses.A_BOLD | curses.A_REVERSE)

    def draw_context(self, left: str, right: str) -> None:
        import curses

        _, width = self.screen.getmaxyx()
        right_column = max(24, width - display_width(right) - 2)
        self.safe_addstr(1, 0, " " * width, curses.A_DIM)
        self.safe_addstr(1, 2, fit_text(left, right_column - 4), curses.A_DIM)
        self.safe_addstr(1, right_column, right, curses.A_DIM)

    @staticmethod
    def set_cursor_visibility(visible: bool) -> None:
        """Set cursor visibility when the current terminal supports it."""
        import curses

        try:
            curses.curs_set(1 if visible else 0)
        except curses.error:
            pass

    def draw(self) -> None:
        import curses

        self.screen.erase()
        height, width = self.screen.getmaxyx()
        if height < 6 or width < 50:
            self.safe_addstr(0, 0, "终端窗口过小，请至少调整到 50×6")
            self.screen.refresh()
            return

        self.draw_navigation()

        if self.view == "data":
            if self.data_mode == "detail":
                self.draw_data_detail()
            elif self.data_mode == "help":
                self.draw_data_help()
            elif self.data_mode == "advice":
                self.draw_data_advice()
            else:
                self.draw_data_list()
        elif self.mode == "detail":
            self.draw_detail()
        elif self.mode == "help":
            self.draw_help()
        else:
            self.draw_list()

        footer = self.message or (
            "Tab 视图  j/k 上下  h 上级  l/Enter 打开  i 说明  R 建议  1-4/<> 排序  ? 帮助"
            if self.view == "data"
            else "Tab 视图  j/k 上下  l/Enter 详情  1-5/<> 排序列  o 顺逆序  d 删除  ? 帮助"
        )
        self.safe_addstr(height - 1, 0, footer, curses.A_REVERSE)
        self.screen.refresh()

    def draw_list(self) -> None:
        import curses

        height, width = self.screen.getmaxyx()
        values = self.visible
        total_size = sum(item.size_bytes for item in values)
        direction = "↓" if self.sort_descending else "↑"
        summary = f"全部会话 {len(values)}/{len(self.sessions)}"
        if self.query:
            summary += f"  搜索:{self.query}"
        self.draw_context(summary, f"{human_size(total_size)}  排序 {self.sort_key}{direction}")

        cwd_width = max(0, min(38, width // 4)) if width >= 110 else 0
        fixed_width = 2 + 9 + 8 + 17 + 2 + cwd_width
        title_width = max(12, width - fixed_width)
        headings = {
            "state": "STATE",
            "size": "SIZE",
            "updated": "UPDATED",
            "title": "NAME / INTRO",
            "cwd": "CWD",
        }
        headings[self.sort_key] += direction
        header = (
            f"  {headings['state']:<8} {headings['size']:>7} "
            f"{headings['updated']:<16}  " + fit_text(headings["title"], title_width)
        )
        if cwd_width:
            header += "  " + fit_text(headings["cwd"], cwd_width)
        self.safe_addstr(2, 0, header, curses.A_UNDERLINE)

        page_size = max(1, height - 4)
        self.offset = min(self.offset, self.selected)
        if self.selected >= self.offset + page_size:
            self.offset = self.selected - page_size + 1
        max_offset = max(0, len(values) - page_size)
        self.offset = min(self.offset, max_offset)

        for screen_row, index in enumerate(
            range(self.offset, min(len(values), self.offset + page_size)), start=3
        ):
            session = values[index]
            marker = "*" if session.session_id in self.marked else " "
            state = session.state
            if session.status.startswith("active"):
                state = "!" + state[:7]
            line = (
                f"{marker} {state:<8} {human_size(session.size_bytes):>7} "
                f"{session.updated.astimezone().strftime('%Y-%m-%d %H:%M')}  "
                + fit_text(session.title, title_width)
            )
            if cwd_width:
                line += "  " + fit_text(shorten_home(session.cwd), cwd_width)
            attribute = curses.A_REVERSE if index == self.selected else curses.A_NORMAL
            self.safe_addstr(screen_row, 0, line, attribute)

        if not values:
            self.safe_addstr(4, 2, "没有匹配的会话")

    def draw_detail(self) -> None:
        import curses

        session = self.current()
        self.draw_context("会话详情", "Tab 切换视图")
        if session is None:
            self.safe_addstr(2, 2, "没有选中的会话")
            return
        fields = [
            ("Name / intro", session.title),
            ("Session ID", session.session_id),
            ("State", session.state),
            ("Runtime status", session.status),
            ("Updated", session.updated.astimezone().isoformat(timespec="seconds")),
            ("Size", f"{human_size(session.size_bytes)} ({session.size_bytes} bytes)"),
            ("Working directory", session.cwd),
            ("Session file", str(session.path) if session.path else "（不可用）"),
        ]
        row = 3
        for label, value in fields:
            self.safe_addstr(row, 2, f"{label}:", curses.A_BOLD)
            self.safe_addstr(row + 1, 4, value)
            row += 2
        self.message = "h/←/Esc/q 返回  r 进入会话  a 归档/取消归档  d 删除"

    def draw_help(self) -> None:
        self.draw_context("会话帮助", "Tab 切换视图")
        lines = [
            "Tab            切换会话 / Codex 数据视图",
            "↑/↓ 或 j/k     移动选择；H/J/K/L 大写同样有效",
            "PgUp/PgDn      翻页；Home/End 或 g/G 跳到首尾",
            "Enter/→/l      查看完整会话信息；详情中 h 返回",
            "/              搜索名称、ID、目录、状态",
            "Space          选择/取消选择，用于批量操作",
            "1–5            按 STATE、SIZE、UPDATED、NAME、CWD 排序",
            "</>            切换到左侧/右侧排序列",
            "o              切换正序（↑）或倒序（↓）",
            "a              归档 active 会话；恢复 archived 会话",
            "d              永久删除；确认时 A 表示本次运行以后全部选择是",
            "r              暂离浏览器并进入当前 Codex 会话",
            "Esc/←          返回列表；在列表中清除搜索",
            "q              退出",
        ]
        for row, line in enumerate(lines, start=3):
            self.safe_addstr(row, 2, line)
        self.message = "按 Esc、←、q 或 ? 返回"

    def draw_data_list(self) -> None:
        import curses

        height, width = self.screen.getmaxyx()
        values = self.data_visible
        total = sum(item.size_bytes for item in self.data_entries)
        direction = "↓" if self.data_descending else "↑"
        location = shorten_home(str(self.data_directory))
        if self.data_query:
            location += f"  搜索:{self.data_query}"
        self.draw_context(location, f"{human_size(total)}  排序 {self.data_sort_key}{direction}")

        first_row = self.data_first_row(height)
        if self.data_directory == self.root:
            visible_advice = self.data_advice_lines if height >= 14 else self.data_advice_lines[:1]
            for row, line in enumerate(visible_advice, start=2):
                self.safe_addstr(row, 2, line, curses.A_BOLD if row == 2 else curses.A_NORMAL)

        name_width = max(12, width - 45)
        labels = ["NAME", "SIZE", "UPDATED", "ACTION"]
        labels[self.DATA_SORT_KEYS.index(self.data_sort_key)] += direction
        heading = (
            "  " + fit_text(f"1 {labels[0]}", name_width) +
            f" {fit_text(f'2 {labels[1]}', 8)} " +
            f"{fit_text(f'3 {labels[2]}', 16)} 4 {labels[3]}"
        )
        self.safe_addstr(first_row - 1, 0, heading, curses.A_UNDERLINE)
        page_size = max(1, height - first_row - 1)
        self.data_offset = min(self.data_offset, self.data_selected)
        if self.data_selected >= self.data_offset + page_size:
            self.data_offset = self.data_selected - page_size + 1
        self.data_offset = min(self.data_offset, max(0, len(values) - page_size))
        for screen_row, index in enumerate(
            range(self.data_offset, min(len(values), self.data_offset + page_size)), start=first_row
        ):
            entry = values[index]
            suffix = "/" if entry.kind == "目录" else "@" if entry.kind == "链接" else ""
            size = human_size(entry.size_bytes) if entry.complete else "?"
            line = (
                "  " + fit_text(entry.path.name + suffix, name_width) +
                f" {size:>8} " +
                f"{entry.updated.astimezone().strftime('%Y-%m-%d %H:%M')} " +
                entry.action
            )
            attribute = curses.A_REVERSE if index == self.data_selected else curses.A_NORMAL
            self.safe_addstr(screen_row, 0, line, attribute)
        if not values:
            self.safe_addstr(first_row, 2, self.data_error or "目录为空或没有匹配项目")

    def data_first_row(self, height: int) -> int:
        if self.data_directory != self.root:
            return 3
        return 7 if height >= 14 else 4

    def draw_data_detail(self) -> None:
        entry = self.data_current()
        self.draw_context("项目说明", "Tab 切换视图")
        if entry is None:
            return
        fields = [
            ("名称", entry.path.name),
            ("路径", shorten_home(str(entry.path))),
            ("类型", entry.kind),
            ("占用估算", human_size(entry.size_bytes) if entry.complete else "扫描不完整"),
            ("修改时间", entry.updated.astimezone().isoformat(timespec="seconds")),
            ("内容", entry.description),
            ("判断依据", entry.basis),
            ("删除影响", entry.impact),
            ("处理方式", entry.action),
        ]
        if entry.kind == "文件" and entry.path.name.endswith(".sqlite"):
            free = sqlite_free_bytes(entry.path)
            if free is not None:
                fields.append(("主文件快照空闲页", f"约 {human_size(free)}；并非保证回收量"))
        for row, (label, value) in enumerate(fields, start=3):
            self.safe_addstr(row, 2, f"{label}: {value}")
        self.message = "h/←/Esc/q 返回  Tab 切换视图  R 查看清理建议"

    def draw_data_advice(self) -> None:
        import curses

        self.draw_context("空间建议", "当前数据只读")
        lines = [
            "按占用与可操作性排序；数字是文件系统或 SQLite 快照的估算。",
            *self.data_advice_lines,
            "会话删除通过 Codex 命令执行；归档只移动会话文件。",
            "SQLite 压缩需独立维护流程；本版不操作数据库、WAL 或 SHM。",
            "插件卸载使用 Codex 官方命令；不要整目录删除 .tmp。",
        ]
        for row, line in enumerate(lines, start=3):
            self.safe_addstr(row, 2, line, curses.A_BOLD if row == 3 else curses.A_NORMAL)
        self.message = "按 h、Esc、←、q 或 R 返回"

    def draw_data_help(self) -> None:
        self.draw_context("Codex 数据帮助", "Tab 切换视图")
        lines = [
            "Tab            切换视图；保留各视图的排序与位置",
            "↑/↓ 或 j/k     移动；H/J/K/L 大写同样有效",
            "Enter/→/l      进入目录；文件打开说明；sessions 跳转会话",
            "i              查看项目用途、依据和删除影响",
            "h/←/Backspace  直接返回上级；Esc 先清除搜索",
            "/              搜索当前目录中的名称",
            "1–4            按 NAME、SIZE、UPDATED、ACTION 排序",
            "</>            左右切换排序列；o 切换升降序",
            "R              查看按当前占用生成的清理建议",
            "d              在 sessions/ 入口跳转会话视图；其他文件只读",
            "q              返回或退出；? 关闭帮助",
        ]
        for row, line in enumerate(lines, start=3):
            self.safe_addstr(row, 2, line)
        self.message = "按 Esc、←、q 或 ? 返回"

    def prompt(self, label: str) -> str:
        import curses

        height, width = self.screen.getmaxyx()
        self.screen.move(height - 1, 0)
        self.screen.clrtoeol()
        self.safe_addstr(height - 1, 0, label, curses.A_REVERSE)
        curses.echo()
        self.set_cursor_visibility(True)
        try:
            raw = self.screen.getstr(height - 1, min(len(label), width - 1), max(1, width - len(label) - 1))
            return raw.decode("utf-8", errors="replace")
        finally:
            curses.noecho()
            self.set_cursor_visibility(False)

    def confirm_delete(self, label: str) -> bool:
        import curses

        if self.delete_confirm_all:
            return True
        height, _ = self.screen.getmaxyx()
        self.screen.move(height - 1, 0)
        self.screen.clrtoeol()
        self.safe_addstr(
            height - 1,
            0,
            label + " [y/N/A=本次运行以后全部是] ",
            curses.A_BOLD | curses.A_REVERSE,
        )
        self.screen.refresh()
        choice = confirmation_choice(self.screen.getch())
        if choice == "all":
            self.delete_confirm_all = True
        return choice in {"yes", "all"}

    def targets(self) -> list[Session]:
        if self.marked:
            return [item for item in self.sessions if item.session_id in self.marked]
        current = self.current()
        return [current] if current is not None else []

    def delete_targets(self) -> None:
        targets = self.targets()
        if not targets:
            return
        total = human_size(sum(item.size_bytes for item in targets))
        active = any(item.status.startswith("active") for item in targets)
        warning = "，包含正在运行的会话" if active else ""
        confirm_all_before = self.delete_confirm_all
        if not self.confirm_delete(
            f"永久删除 {len(targets)} 个会话（{total}）{warning}？"
        ):
            self.message = "已取消删除"
            return

        deleted: set[str] = set()
        errors: list[str] = []
        for session in targets:
            ok, detail = run_codex_action(["delete", "--force", session.session_id])
            if ok:
                deleted.add(session.session_id)
            else:
                errors.append(f"{session.session_id[:8]}: {detail}")
        self.sessions = [item for item in self.sessions if item.session_id not in deleted]
        self.marked.difference_update(deleted)
        self.selected = min(self.selected, max(0, len(self.visible) - 1))
        self.message = f"已删除 {len(deleted)} 个会话"
        if not confirm_all_before and self.delete_confirm_all:
            self.message += "；本次运行后续删除不再确认"
        if errors:
            self.message += f"；失败 {len(errors)} 个：{errors[0]}"

    def toggle_archive(self) -> None:
        anchor = self.current()
        targets = self.targets()
        if not targets:
            return
        changed: dict[str, str] = {}
        errors: list[str] = []
        for session in targets:
            action = "unarchive" if session.state == "archived" else "archive"
            ok, detail = run_codex_action([action, session.session_id])
            if ok:
                changed[session.session_id] = (
                    "active" if session.state == "archived" else "archived"
                )
            else:
                errors.append(f"{session.session_id[:8]}: {detail}")
        self.sessions = [
            replace(
                item,
                state=changed.get(item.session_id, item.state),
                path=None if item.session_id in changed else item.path,
            )
            for item in self.sessions
        ]
        self.marked.difference_update(changed)
        if anchor is not None:
            self.select_session(anchor.session_id)
        self.message = f"已更新 {len(changed)} 个会话的归档状态"
        if errors:
            self.message += f"；失败 {len(errors)} 个：{errors[0]}"

    def resume_current(self) -> None:
        import curses

        session = self.current()
        executable = codex_executable()
        if session is None or executable is None:
            self.message = "没有可进入的会话，或找不到 codex 命令"
            return
        command = [executable]
        command.extend(["resume", session.session_id])
        working_directory = session.cwd if Path(session.cwd).is_dir() else None
        curses.def_prog_mode()
        curses.endwin()
        try:
            result = subprocess.run(command, cwd=working_directory, check=False)
            self.message = f"Codex 会话已退出（状态码 {result.returncode}）"
        except OSError as error:
            self.message = str(error)
        finally:
            curses.reset_prog_mode()
            self.screen.refresh()

    def move(self, amount: int) -> None:
        values = self.visible
        if values:
            self.selected = max(0, min(len(values) - 1, self.selected + amount))

    def data_move(self, amount: int) -> None:
        values = self.data_visible
        if values:
            self.data_selected = max(
                0, min(len(values) - 1, self.data_selected + amount)
            )

    def data_open(self) -> None:
        entry = self.data_current()
        if entry is None:
            return
        relative = entry.path.relative_to(self.root)
        if len(relative.parts) == 1 and relative.name in {
            "sessions", "archived_sessions"
        }:
            self.view = "sessions"
            self.mode = "list"
            self.sort_key = "size"
            self.sort_descending = True
            self.query = "archived" if relative.name == "archived_sessions" else ""
            self.selected = 0
            self.offset = 0
        elif entry.kind == "目录":
            self.data_directory = entry.path
            self.data_query = ""
            self.data_selected = 0
            self.data_offset = 0
            self.refresh_data()
        else:
            self.data_mode = "detail"

    def data_back(self) -> None:
        if self.data_directory != self.root:
            previous = self.data_directory
            self.data_directory = previous.parent
            self.data_query = ""
            self.data_offset = 0
            self.refresh_data()
            for index, entry in enumerate(self.data_visible):
                if entry.path == previous:
                    self.data_selected = index
                    break

    def handle_data_key(self, key: int) -> bool:
        import curses

        if self.data_mode in {"detail", "help", "advice"}:
            if key in (
                27, curses.KEY_LEFT, curses.KEY_BACKSPACE, 8, 127,
                ord("h"), ord("H"), ord("q"), ord("?"), ord("R"),
            ):
                self.data_mode = "list"
            return True

        height, _ = self.screen.getmaxyx()
        page = max(1, height - self.data_first_row(height) - 1)
        if key == ord("q"):
            if self.data_directory == self.root:
                return False
            self.data_back()
        elif key in (curses.KEY_DOWN, ord("j"), ord("J")):
            self.data_move(1)
        elif key in (curses.KEY_UP, ord("k"), ord("K")):
            self.data_move(-1)
        elif key == curses.KEY_NPAGE:
            self.data_move(page)
        elif key == curses.KEY_PPAGE:
            self.data_move(-page)
        elif key in (curses.KEY_HOME, ord("g")):
            self.data_selected = 0
        elif key in (curses.KEY_END, ord("G")):
            self.data_selected = max(0, len(self.data_visible) - 1)
        elif key in (10, 13, curses.KEY_RIGHT, ord("l"), ord("L")):
            self.data_open()
        elif key == ord("i"):
            if self.data_current() is not None:
                self.data_mode = "detail"
        elif key == ord("?"):
            self.data_mode = "help"
        elif key == ord("R"):
            self.data_mode = "advice"
        elif key == ord("/"):
            self.data_query = self.prompt("搜索当前目录: ").strip()
            self.data_selected = 0
            self.data_offset = 0
        elif key == 27 and self.data_query:
            self.data_query = ""
            self.data_selected = 0
            self.data_offset = 0
        elif key in (
            27, curses.KEY_LEFT, curses.KEY_BACKSPACE, 8, 127, ord("h"), ord("H")
        ):
            self.data_back()
        elif ord("1") <= key <= ord("4"):
            self.data_sort_key = self.DATA_SORT_KEYS[key - ord("1")]
            self.data_selected = 0
            self.data_offset = 0
        elif key in (ord("<"), ord(","), ord(">"), ord(".")):
            step = -1 if key in (ord("<"), ord(",")) else 1
            index = (self.DATA_SORT_KEYS.index(self.data_sort_key) + step) % 4
            self.data_sort_key = self.DATA_SORT_KEYS[index]
            self.data_selected = 0
            self.data_offset = 0
        elif key == ord("o"):
            self.data_descending = not self.data_descending
            self.data_selected = 0
            self.data_offset = 0
        elif key == ord("d"):
            entry = self.data_current()
            if entry is not None and entry.action == "会话":
                self.data_open()
                self.message = "按大小选择会话，再按 d 通过 Codex 删除"
            else:
                self.message = "该数据文件只读；会话请通过 Codex 删除"
        return True

    def handle_key(self, key: int) -> bool:
        import curses

        self.message = ""
        if key == 9:
            self.switch_view()
            return True
        if self.view == "data":
            return self.handle_data_key(key)
        if self.mode in {"detail", "help"}:
            if key in (27, curses.KEY_LEFT, ord("h"), ord("H"), ord("q"), ord("?")):
                self.mode = "list"
                return True
            if self.mode == "detail" and key == ord("d"):
                self.delete_targets()
                self.mode = "list"
            elif self.mode == "detail" and key == ord("a"):
                self.toggle_archive()
            elif self.mode == "detail" and key == ord("r"):
                self.resume_current()
            return True

        height, _ = self.screen.getmaxyx()
        page = max(1, height - 4)
        if key in (ord("q"),):
            return False
        if key in (curses.KEY_DOWN, ord("j"), ord("J")):
            self.move(1)
        elif key in (curses.KEY_UP, ord("k"), ord("K")):
            self.move(-1)
        elif key == curses.KEY_NPAGE:
            self.move(page)
        elif key == curses.KEY_PPAGE:
            self.move(-page)
        elif key in (curses.KEY_HOME, ord("g")):
            self.selected = 0
        elif key in (curses.KEY_END, ord("G")):
            self.selected = max(0, len(self.visible) - 1)
        elif key in (10, 13, curses.KEY_RIGHT, ord("l"), ord("L")):
            if self.current() is not None:
                self.mode = "detail"
        elif key == ord("?"):
            self.mode = "help"
        elif key == ord("/"):
            self.query = self.prompt("搜索: ").strip()
            self.selected = 0
            self.offset = 0
        elif key in (27, curses.KEY_LEFT):
            self.query = ""
            self.selected = 0
            self.offset = 0
        elif key == ord(" "):
            current = self.current()
            if current is not None:
                if current.session_id in self.marked:
                    self.marked.remove(current.session_id)
                else:
                    self.marked.add(current.session_id)
                self.move(1)
        elif ord("1") <= key <= ord("5"):
            self.sort_key = self.SORT_KEYS[key - ord("1")]
            self.selected = 0
            self.offset = 0
        elif key in (ord("<"), ord(",")):
            index = (self.SORT_KEYS.index(self.sort_key) - 1) % len(self.SORT_KEYS)
            self.sort_key = self.SORT_KEYS[index]
            self.selected = 0
            self.offset = 0
        elif key in (ord(">"), ord(".")):
            index = (self.SORT_KEYS.index(self.sort_key) + 1) % len(self.SORT_KEYS)
            self.sort_key = self.SORT_KEYS[index]
            self.selected = 0
            self.offset = 0
        elif key == ord("o"):
            self.sort_descending = not self.sort_descending
            self.selected = 0
            self.offset = 0
        elif key == ord("d"):
            self.delete_targets()
        elif key == ord("a"):
            self.toggle_archive()
        elif key == ord("r"):
            self.resume_current()
        return True

    def run(self) -> None:
        self.set_cursor_visibility(False)
        self.screen.keypad(True)
        while True:
            self.draw()
            if not self.handle_key(self.screen.getch()):
                break


def run_tui(sessions: Sequence[Session], root: Path | None = None) -> None:
    """Open the interactive session and CODEX_HOME browser."""
    try:
        import curses
    except ImportError as error:
        detail = "；Windows 请安装 windows-curses" if os.name == "nt" else ""
        raise RuntimeError(
            f"当前 Python 环境不支持 curses{detail}，或使用 --plain"
        ) from error
    curses.wrapper(lambda screen: SessionBrowser(screen, sessions, root).run())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codu",
        description="列出本机保存的 Codex 会话，按最近更新时间倒序排列。"
    )
    parser.add_argument(
        "directory",
        nargs="?",
        help="仅列出工作目录与此路径完全匹配的会话；省略则列出全部会话",
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--json",
        action="store_true",
        help="输出完整 JSON（包含字节数和会话文件路径）",
    )
    output.add_argument(
        "--plain",
        action="store_true",
        help="输出制表符分隔列表，不进入交互界面",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="在标准错误中显示 app-server 回退原因",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    directory = canonical_directory(args.directory) if args.directory else None

    try:
        sessions = discover_sessions_from_app_server(directory)
    except AppServerError as error:
        if args.verbose:
            print(
                f"codu: app-server 不可用，回退到文件扫描：{error}",
                file=sys.stderr,
            )
        sessions = discover_sessions(root, directory)

    if args.json:
        print_json(sessions)
    elif args.plain or not (sys.stdin.isatty() and sys.stdout.isatty()):
        print_tsv(sessions)
    else:
        try:
            run_tui(sessions, root)
        except RuntimeError as error:
            print(f"codu: {error}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
