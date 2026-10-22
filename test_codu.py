import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import codu


def write_session(
    path: Path,
    session_id: str,
    cwd: Path,
    prompt: str = "Fix the parser",
    updated: str = "2026-09-24T08:30:00Z",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    events = [
        {
            "timestamp": "2026-09-24T08:00:00Z",
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "timestamp": "2026-09-24T08:00:00Z",
                "cwd": str(cwd),
            },
        },
        {
            "timestamp": "2026-09-24T08:00:01Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "<environment_context>ignored"}
                ],
            },
        },
        {
            "timestamp": updated,
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            },
        },
    ]
    path.write_text(
        "not-json\n" + "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )


def write_fake_codex(directory: Path) -> Path:
    """Create a cross-platform fake Codex executable for transport tests."""
    program = """\
import json
import sys

for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    if message.get("method") == "thread/list":
        result = {"data": [], "nextCursor": None}
    else:
        result = {}
    print(json.dumps({"id": message["id"], "result": result}), flush=True)
"""
    if os.name == "nt":
        script = directory / "fake_codex.py"
        script.write_text(program, encoding="utf-8")
        launcher = directory / "codex.cmd"
        command = subprocess.list2cmdline([sys.executable, str(script)])
        launcher.write_text(f"@{command} %*\n", encoding="utf-8")
        return launcher

    launcher = directory / "codex"
    launcher.write_text(f"#!{sys.executable}\n{program}", encoding="utf-8")
    launcher.chmod(0o755)
    return launcher


class CoduTests(unittest.TestCase):
    def test_data_view_classifies_entries_and_counts_allocated_space(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "sessions").mkdir()
            (root / "sessions" / "large.jsonl").write_bytes(b"x" * 100_000)
            (root / "cache" / "remote_plugin_catalog").mkdir(parents=True)
            (root / "cache" / "remote_plugin_catalog" / "index").write_bytes(b"x")
            (root / "auth.json").write_text("credential", encoding="utf-8")
            (root / "unknown").write_text("unknown", encoding="utf-8")
            rows = {row.path.name: row for row in codu.list_data_entries(root, root)}

            self.assertEqual(rows["sessions"].action, "会话")
            self.assertEqual(rows["auth.json"].action, "只读")
            self.assertEqual(rows["unknown"].basis, "未确认")
            self.assertGreaterEqual(rows["sessions"].size_bytes, 4096)
            cache_rows = codu.list_data_entries(root, root / "cache")
            self.assertEqual(cache_rows[0].action, "核查")
            self.assertEqual(
                codu.filter_and_sort_data(list(rows.values()), "", "size", True)[0].path.name,
                "sessions",
            )

    def test_cache_scan_does_not_follow_symlinks_or_offer_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "codex"
            outside = Path(temp) / "outside"
            outside.mkdir()
            (outside / "keep").write_bytes(b"x" * 1_000_000)
            cache = root / "cache" / "remote_plugin_catalog"
            cache.mkdir(parents=True)
            (cache / "file").write_text("cache", encoding="utf-8")
            try:
                (cache / "link").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                pass
            entry = codu.list_data_entries(root, root / "cache")[0]
            self.assertEqual(entry.action, "核查")
            self.assertLess(entry.size_bytes, 1_000_000)
            self.assertTrue(cache.exists())
            self.assertTrue((outside / "keep").exists())

    def test_keyboard_switches_views_and_uses_same_sort_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "cache").mkdir()
            screen = Mock()
            screen.getmaxyx.return_value = (24, 100)
            browser = codu.SessionBrowser(screen, [], root)
            browser.handle_key(9)
            self.assertEqual(browser.view, "data")
            self.assertEqual(browser.data_sort_key, "size")
            browser.handle_key(ord("1"))
            self.assertEqual(browser.data_sort_key, "name")
            browser.handle_key(ord(">"))
            self.assertEqual(browser.data_sort_key, "size")
            browser.handle_key(ord("o"))
            self.assertFalse(browser.data_descending)
            browser.handle_key(9)
            self.assertEqual(browser.view, "sessions")
            browser.handle_key(ord("2"))
            self.assertEqual(browser.sort_key, "size")
            browser.handle_key(ord("<"))
            self.assertEqual(browser.sort_key, "state")
            browser.handle_key(9)
            self.assertEqual(browser.data_sort_key, "size")
            self.assertFalse(browser.data_descending)

    def test_data_navigation_links_sessions_and_keeps_read_only_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "sessions").mkdir()
            (root / "cache" / "remote_plugin_catalog").mkdir(parents=True)
            (root / "cache" / "other").write_text("unknown", encoding="utf-8")
            screen = Mock()
            screen.getmaxyx.return_value = (24, 100)
            browser = codu.SessionBrowser(screen, [], root)
            browser.handle_key(9)
            browser.data_sort_key = "name"
            browser.data_selected = next(
                index for index, entry in enumerate(browser.data_visible)
                if entry.path.name == "cache"
            )
            browser.data_open()
            self.assertEqual(browser.data_directory, root / "cache")
            self.assertEqual(
                next(entry for entry in browser.data_entries if entry.path.name == "other").action,
                "只读",
            )
            browser.data_back()
            self.assertEqual(browser.data_directory, root)
            browser.data_selected = next(
                index for index, entry in enumerate(browser.data_visible)
                if entry.path.name == "sessions"
            )
            browser.data_open()
            self.assertEqual(browser.view, "sessions")
            self.assertEqual(browser.sort_key, "size")
            self.assertTrue(browser.sort_descending)

    def test_vim_keys_navigate_data_directories_and_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "cache").mkdir()
            (root / "cache" / "item.txt").write_text("keep", encoding="utf-8")
            screen = Mock()
            screen.getmaxyx.return_value = (24, 100)
            browser = codu.SessionBrowser(screen, [], root)
            browser.handle_key(9)
            browser.data_sort_key = "name"
            browser.handle_key(ord("L"))
            self.assertEqual(browser.data_directory, root / "cache")

            browser.data_query = "item"
            browser.handle_key(ord("H"))
            self.assertEqual(browser.data_directory, root)
            self.assertEqual(browser.data_query, "")
            browser.handle_key(ord("h"))
            self.assertEqual(browser.data_directory, root)

            browser.handle_key(ord("l"))
            browser.handle_key(ord("l"))
            self.assertEqual(browser.data_mode, "detail")
            browser.handle_key(ord("h"))
            self.assertEqual(browser.data_mode, "list")

            browser.data_query = "item"
            browser.handle_key(27)
            self.assertEqual(browser.data_query, "")
            self.assertEqual(browser.data_directory, root / "cache")
            browser.handle_key(ord("j"))
            browser.handle_key(ord("K"))
            self.assertEqual(browser.data_selected, 0)

    def test_vim_keys_open_and_leave_session_details(self) -> None:
        session = codu.Session(
            updated=codu.datetime.fromisoformat("2026-09-24T08:00:00+00:00"),
            state="active", status="notLoaded", session_id="first",
            size_bytes=10, title="First", cwd="/repo", path=None,
        )
        screen = Mock()
        screen.getmaxyx.return_value = (24, 100)
        browser = codu.SessionBrowser(screen, [session])
        browser.handle_key(ord("l"))
        self.assertEqual(browser.mode, "detail")
        browser.handle_key(ord("H"))
        self.assertEqual(browser.mode, "list")
        browser.handle_key(ord("h"))
        self.assertEqual(browser.mode, "list")

    def test_data_view_does_not_delete_unknown_or_cache_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "unknown").write_text("preserve", encoding="utf-8")
            screen = Mock()
            screen.getmaxyx.return_value = (24, 100)
            browser = codu.SessionBrowser(screen, [], root)
            browser.handle_key(9)
            browser.data_sort_key = "name"
            browser.data_selected = 0
            browser.handle_key(ord("d"))
            self.assertTrue((root / "unknown").exists())
            self.assertIn("只读", browser.message)

            cache = root / "cache" / "remote_plugin_catalog"
            cache.mkdir(parents=True)
            browser.data_directory = root / "cache"
            browser.refresh_data()
            browser.handle_key(ord("d"))
            self.assertTrue(cache.exists())
            screen.getch.assert_not_called()

    def test_sqlite_free_pages_are_explained_without_database_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "thread_history_1.sqlite"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("CREATE TABLE thread_items (item_json TEXT)")
                connection.executemany(
                    "INSERT INTO thread_items VALUES (?)", [("x" * 5000,)] * 40
                )
                connection.execute("DELETE FROM thread_items")
                connection.commit()
            free = codu.sqlite_free_bytes(path)
            self.assertIsNotNone(free)
            assert free is not None
            self.assertGreater(free, 0)
            rows = codu.list_data_entries(root, root)
            self.assertIn("分页历史库", rows[0].description)
            self.assertEqual(rows[0].action, "只读")
            self.assertTrue(any("数据库空闲页" in line for line in codu.data_advice(rows)))

    def test_discovers_filters_and_prefers_latest_renamed_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            other = root / "other"
            write_session(root / "sessions/2026/09/a.jsonl", "session-a", project)
            write_session(
                root / "archived_sessions/b.jsonl",
                "session-b",
                other,
                updated="2026-09-23T08:30:00Z",
            )
            (root / "session_index.jsonl").write_text(
                json.dumps({"id": "session-a", "thread_name": "old"})
                + "\n"
                + json.dumps({"id": "session-a", "thread_name": "New title"})
                + "\n",
                encoding="utf-8",
            )

            sessions = codu.discover_sessions(
                root, codu.canonical_directory(project)
            )

            self.assertEqual([session.session_id for session in sessions], ["session-a"])
            self.assertEqual(sessions[0].title, "New title")
            self.assertEqual(sessions[0].state, "active")

    def test_intro_and_event_timestamp_are_used_as_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_session(
                root / "sessions/a.jsonl",
                "session-a",
                root,
                prompt="  Explain\n   this session  ",
            )

            session = codu.discover_sessions(root)[0]

            self.assertEqual(session.title, "Explain this session")
            self.assertEqual(session.updated.isoformat(), "2026-09-24T08:30:00+00:00")

    def test_main_without_directory_lists_all_and_json_includes_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_session(root / "sessions/a.jsonl", "session-a", root)
            output = io.StringIO()

            with (
                patch.dict(os.environ, {"CODEX_HOME": str(root)}),
                patch.object(
                    codu,
                    "discover_sessions_from_app_server",
                    side_effect=codu.AppServerError("not available"),
                ),
                contextlib.redirect_stdout(output),
            ):
                result = codu.main(["--json"])

            values = json.loads(output.getvalue())
            self.assertEqual(result, 0)
            self.assertEqual(values[0]["session_id"], "session-a")
            self.assertEqual(values[0]["cwd"], str(root))
            self.assertEqual(
                Path(values[0]["path"]), root / "sessions" / "a.jsonl"
            )

    def test_app_server_thread_fields_include_runtime_status_and_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "session.jsonl"
            path.write_bytes(b"12345")

            session = codu.session_from_thread(
                {
                    "id": "session-a",
                    "cwd": temp,
                    "updatedAt": 1_790_251_200,
                    "path": str(path),
                    "name": "Renamed session",
                    "preview": "Original prompt",
                    "status": {
                        "type": "active",
                        "activeFlags": ["waitingOnApproval"],
                    },
                },
                "active",
            )

            self.assertIsNotNone(session)
            assert session is not None
            self.assertEqual(session.title, "Renamed session")
            self.assertEqual(session.status, "active:waitingOnApproval")
            self.assertEqual(session.size_bytes, 5)

    def test_app_server_launches_platform_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            executable = write_fake_codex(Path(temp))

            with (
                patch.dict(os.environ, {"CODEX_BIN": ""}),
                patch.object(codu.shutil, "which", return_value=str(executable)),
            ):
                sessions = codu.discover_sessions_from_app_server()

            self.assertEqual(sessions, [])

    def test_browser_filter_sort_and_wide_character_clipping(self) -> None:
        first = codu.Session(
            updated=codu.datetime.fromisoformat("2026-09-24T08:00:00+00:00"),
            state="active",
            status="notLoaded",
            session_id="first",
            size_bytes=10,
            title="中文会话",
            cwd="/repo/one",
            path=None,
        )
        second = codu.Session(
            updated=codu.datetime.fromisoformat("2026-09-24T09:00:00+00:00"),
            state="archived",
            status="notLoaded",
            session_id="second",
            size_bytes=20,
            title="Parser work",
            cwd="/repo/two",
            path=None,
        )

        filtered = codu.filter_and_sort_sessions(
            [first, second], "repo/one", "updated"
        )
        by_size = codu.filter_and_sort_sessions([first, second], "", "size")
        by_size_ascending = codu.filter_and_sort_sessions(
            [first, second], "", "size", descending=False
        )

        self.assertEqual([item.session_id for item in filtered], ["first"])
        self.assertEqual([item.session_id for item in by_size], ["second", "first"])
        self.assertEqual(
            [item.session_id for item in by_size_ascending], ["first", "second"]
        )
        self.assertEqual(codu.display_width("中文a"), 5)
        self.assertEqual(codu.display_width(codu.fit_text("中文a", 4)), 4)

    def test_delete_confirmation_has_yes_no_and_yes_to_all(self) -> None:
        self.assertEqual(codu.confirmation_choice(ord("y")), "yes")
        self.assertEqual(codu.confirmation_choice(ord("A")), "all")
        self.assertEqual(codu.confirmation_choice(ord("n")), "no")

    def test_archiving_preserves_selected_session_when_state_sort_reorders(self) -> None:
        active = codu.Session(
            updated=codu.datetime.fromisoformat("2026-09-24T08:00:00+00:00"),
            state="active",
            status="notLoaded",
            session_id="active-session",
            size_bytes=10,
            title="Active",
            cwd="/repo",
            path=None,
        )
        archived = codu.Session(
            updated=codu.datetime.fromisoformat("2026-09-24T09:00:00+00:00"),
            state="archived",
            status="notLoaded",
            session_id="archived-session",
            size_bytes=20,
            title="Archived",
            cwd="/repo",
            path=None,
        )
        browser = codu.SessionBrowser(object(), [active, archived])
        browser.sort_key = "state"
        browser.sort_descending = True
        browser.selected = 1
        self.assertEqual(browser.current().session_id, "active-session")

        with patch.object(codu, "run_codex_action", return_value=(True, "")):
            browser.toggle_archive()

        self.assertEqual(browser.current().session_id, "active-session")

    def test_app_server_timeout_cleans_up_process(self) -> None:
        client = codu.AppServerClient(
            [
                sys.executable,
                "-c",
                "import sys,time; sys.stdin.readline(); time.sleep(10)",
            ],
            timeout=0.05,
        )

        with self.assertRaisesRegex(codu.AppServerError, "timed out"):
            client.__enter__()

        self.assertIsNotNone(client.process.poll())

    def test_app_server_drains_large_stderr_without_deadlock(self) -> None:
        script = (
            "import json,sys; "
            "request=json.loads(sys.stdin.readline()); "
            "sys.stderr.write('x'*200000); sys.stderr.flush(); "
            "print(json.dumps({'id':request['id'],'result':{}}),flush=True); "
            "sys.stdin.readline()"
        )

        with codu.AppServerClient(
            [sys.executable, "-c", script], timeout=2
        ) as client:
            pass

        self.assertGreater(len("".join(client.stderr_lines)), 100000)

    def test_codex_action_timeout_is_reported(self) -> None:
        with (
            patch.object(codu, "codex_executable", return_value="codex"),
            patch.object(
                subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["codex", "archive"], 1),
            ),
        ):
            success, message = codu.run_codex_action(["archive", "session-a"])

        self.assertFalse(success)
        self.assertIn("超时", message)

    def test_resume_uses_the_session_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            session = codu.Session(
                updated=codu.datetime.now(codu.UTC),
                state="active",
                status="notLoaded",
                session_id="session-a",
                size_bytes=0,
                title="Session",
                cwd=temp,
                path=None,
            )
            browser = codu.SessionBrowser(Mock(), [session])
            result = subprocess.CompletedProcess([], 0)

            with (
                patch.object(codu, "codex_executable", return_value="codex"),
                patch.object(subprocess, "run", return_value=result) as run,
                patch("curses.def_prog_mode"),
                patch("curses.endwin"),
                patch("curses.reset_prog_mode"),
            ):
                browser.resume_current()

            run.assert_called_once_with(
                ["codex", "resume", "session-a"], cwd=temp, check=False
            )
            browser.screen.refresh.assert_called_once_with()

    def test_version_flag_reports_package_version(self) -> None:
        output = io.StringIO()

        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as error:
            codu.main(["--version"])

        self.assertEqual(error.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), f"codu {codu.__version__}")


if __name__ == "__main__":
    unittest.main()
