# codu

[简体中文](README.md) | [English](README.en.md)

A terminal browser for local Codex sessions. Inspired by `ncdu` and `top`, it helps you inspect session timestamps, IDs, working directories, names or prompt previews, states, and transcript sizes, then archive, restore, or delete sessions after confirmation.

> [!WARNING]
> This project is experimental. Deletion is permanent. Read the [Security model](#security-model) and [Known limitations](#known-limitations) before relying on it for routine cleanup.

This is an unofficial community project and is not affiliated with OpenAI.

## Features

- Full-screen, `ncdu`-style terminal UI with arrow-key and `j` / `k` navigation
- Active/archived state, runtime status, update time, session ID, working directory, title/preview, and JSONL file size
- Ascending or descending sort by state, size, update time, title, or working directory
- Search by title, ID, directory, or status
- Multi-select archive, restore, and permanent delete operations
- Session-scoped “yes to all” delete confirmation
- Resume the selected Codex session directly from the browser
- Non-interactive TSV and JSON output
- Codex app-server first, with a local transcript scan fallback
- Python standard library only on macOS and Linux; `windows-curses` on Windows

## Requirements

| Component | Requirement |
| --- | --- |
| Python | 3.11 or newer |
| Codex CLI | The `codex` command is on `PATH` and configured normally |
| Interactive terminal | macOS, Linux, or Windows Terminal (x64) |

Package installation automatically installs `windows-curses` on Windows. When running directly from source with a Python build that cannot import `curses`, run `py -m pip install windows-curses` first. The `--plain` and `--json` modes do not require a full-screen terminal.
Native Windows support currently targets x64 Python because `windows-curses` does not provide a native ARM64 wheel.

Session operations require the Codex CLI commands `app-server`, `archive`, `unarchive`, `delete`, and `resume`. Capabilities can differ between Codex versions.

## Installation

### Homebrew (macOS, recommended)

Install through the `ActivationEnergy/cask` tap:

```console
brew tap ActivationEnergy/cask
brew install codu
```

The formula source is [`packaging/homebrew/codu.rb`](packaging/homebrew/codu.rb). It downloads one versioned single-file entry point, verifies its SHA256, and runs it with Homebrew Python; it does not build a Python package.

### uv / pip / pipx (Windows and Linux)

The recommended path is to install the versioned universal wheel with `uv`:

```console
uv tool install https://github.com/ActivationEnergy/codu/releases/download/v0.2.0/codu-0.2.0-py3-none-any.whl
codu --version
```

Regular `pip` and `pipx` are also supported:

```console
python -m pip install https://github.com/ActivationEnergy/codu/releases/download/v0.2.0/codu-0.2.0-py3-none-any.whl
pipx install https://github.com/ActivationEnergy/codu/releases/download/v0.2.0/codu-0.2.0-py3-none-any.whl
```

On Windows, use Windows Terminal with PowerShell; replace `python` with `py` in the command above when needed.

### Run from source

On macOS or Linux:

```console
chmod +x codu
./codu
```

On Windows PowerShell:

```powershell
py -m pip install .
codu
```

## Quick start

```console
# Browse all sessions
codu

# Browse sessions whose working directory matches exactly
codu /path/to/project

# Current directory
codu .

# Tab-separated output
codu --plain

# JSON output
codu /path/to/project --json
```

`directory` can be absolute, relative, or start with `~`. It is normalized and matched exactly against the recorded working directory; child directories are not included automatically.

When stdin or stdout is not a terminal, codu automatically uses the `--plain` format.

## Command-line interface

```text
usage: codu [-h] [--json | --plain] [--verbose] [--version] [directory]
```

| Argument | Description |
| --- | --- |
| `directory` | Optional. Only list sessions with an exactly matching working directory |
| `--plain` | Print tab-separated rows instead of opening the full-screen UI |
| `--json` | Print complete JSON, including byte sizes and transcript paths |
| `--verbose` | Explain app-server failure and file-scan fallback on stderr |
| `--version` | Show the codu version |
| `-h`, `--help` | Show help |

## Interactive keys

### Navigation

| Key | Action |
| --- | --- |
| `↑` / `↓`, `j` / `k` | Move up/down |
| `PgUp` / `PgDn` | Move one page |
| `Home` / `End`, `g` / `G` | Jump to first/last row |
| `Enter`, `→` | Open session details |
| `Esc`, `←` | Return to the list; clear search from the list |
| `/` | Search title, ID, directory, or status |
| `Space` | Mark or unmark the current session |
| `?` | Open help |
| `q` | Go back or quit |

### Sorting

| Key | Sort column |
| --- | --- |
| `1` | STATE |
| `2` | SIZE |
| `3` | UPDATED |
| `4` | NAME / INTRO |
| `5` | CWD |
| `<` / `>` | Move to the previous/next sort column |
| `o` | Toggle ascending `↑` / descending `↓` |

The active sort column and direction appear in both the status bar and column header.

### Session actions

| Key | Action |
| --- | --- |
| `a` | Archive an active session or restore an archived session |
| `d` | Permanently delete selected sessions |
| `r` | Resume the current Codex session; return after it exits |

If one or more sessions are marked with `Space`, `a` and `d` act on **all marked sessions**, not the cursor row.

Delete confirmation:

- `y`: confirm this deletion only
- `N` or any other key: cancel
- `A`: confirm now and automatically confirm later deletions during this codu process

`A` is not persisted. Normal per-operation confirmation returns after codu exits.

## Data sources and behavior

codu discovers sessions in this order:

1. Connect to an existing shared app-server through `codex app-server proxy`.
2. Start a temporary `codex app-server` when no shared service is available.
3. If the app-server interface is unavailable, scan `$CODEX_HOME/sessions` and `$CODEX_HOME/archived_sessions`; `CODEX_HOME` defaults to `~/.codex`.

The app-server path uses paginated `thread/list` calls and its exact working-directory filter. Titles, previews, status, timestamps, and transcript paths come from Codex. The compatibility fallback reads JSONL metadata and uses `session_index.jsonl` for names set through `/rename`.

| Environment variable | Purpose |
| --- | --- |
| `CODEX_HOME` | Override the Codex data directory; defaults to `~/.codex` |
| `CODEX_BIN` | Override the path to the `codex` executable |
| `CODU_TIMEOUT` | Timeout in seconds for app-server and delete/archive operations; default `30` |

## Output fields

| Field | Meaning |
| --- | --- |
| `state` / STATE | `active` or `archived` |
| `status` / STATUS | Runtime status reported by app-server; `unknown` in file fallback mode |
| `updated` / UPDATED | Latest update time, displayed in the local timezone |
| `session_id` / SESSION_ID | Codex session/thread ID |
| `size_bytes` / SIZE | Size of the current session JSONL file |
| `title` / NAME / INTRO | `/rename` title or the first recognizable user request |
| `cwd` / CWD | Recorded working directory |
| `path` | Transcript JSONL path; JSON output only |

```console
codu --json | jq '.[] | select(.size_bytes > 1048576)'
codu --plain | column -t -s $'\t'
```

## Security model

- Browsing, filtering, and exporting do not directly modify transcript logs. app-server may maintain or repair its own metadata indexes.
- Archive and restore operations are reversible and run through the Codex CLI.
- Delete runs `codex delete --force <UUID>` and is permanent. Under Codex thread deletion semantics, it may also delete descendant threads.
- `STATUS=active...` only means the **connected app-server** considers the session active. `notLoaded` or `unknown` does not prove another Codex process is not using it.
- `A` disables later confirmations only for the current process. Once enabled, pay close attention to the cursor and marked sessions.
- codu passes arguments to `codex` as an argument array; it does not interpolate session IDs or paths into a shell command.

Archive uncertain sessions first, then delete them only after confirming they are no longer needed.

## Known limitations

- SIZE counts only the current thread JSONL file. It excludes attachments, generated images, caches, and descendant threads that deletion may also remove; it is not an exact “disk space reclaimed” value.
- A newly started local app-server only knows its own runtime state and may not see activity in other Codex processes.
- The file-scan fallback depends on Codex's current local JSONL format and may require updates when that format changes.
- Windows full-screen mode targets Windows Terminal and PowerShell; legacy console hosts are not supported.
- Native Windows on ARM64 Python is not yet supported; ARM devices can use an emulated x64 Python environment.
- Automated tests cover installation, `curses` import, and core logic on all three platforms, but the complete terminal key state machine still requires manual terminal testing.
- app-server requests and delete/archive operations time out after 30 seconds by default. Interactive `resume` is intentionally not subject to that timeout.

## Troubleshooting

### No sessions are shown

```console
codex --version
codu --plain
```

Directory filtering is exact. Omit the directory first to confirm that the session exists.

### The full-screen UI does not start

```console
codu --plain
python3 -c 'import curses; print("curses OK")'
```

### Delete, archive, or restore fails

```console
codex delete --help
codex archive --help
codex unarchive --help
```

### CJK or wide characters are misaligned

Use a UTF-8 locale and a terminal font with proper wide-character support. codu estimates width with Unicode East Asian Width; complex emoji and some combining sequences can still be misaligned.

## Development and verification

```console
python3 -m pip install -e .
python3 -m unittest -v

# Optional
ruff check codu.py test_codu.py
```

CI runs on Linux, macOS, and Windows with Python 3.11 and 3.14, plus Python 3.12 and 3.13 on Linux. Current tests cover discovery, directory filtering, renamed-title and preview fallback, timestamp parsing, JSON output, app-server mapping and lifecycle, request timeout, stderr draining, selection preservation after archive, cross-platform resume invocation, filtering and sorting, wide-character clipping, and confirmation key mapping. Real archive/delete operations and the complete curses state machine still need automated coverage.

## Contributing

Issues and pull requests are welcome. When fixing a bug or changing behavior:

1. Include the reproducible Codex CLI version, Python version, and operating system.
2. Add a regression test for important behavior changes and bug fixes.
3. Run `python3 -m unittest -v`, plus `ruff check codu.py test_codu.py` when Ruff is available.
4. Never include real session content, access tokens, or other sensitive data in issues or fixtures.

## Pre-release checklist

- [x] Use the MIT License and add `LICENSE` at the repository root
- [x] Add CI for Python 3.11–3.14 on Linux/macOS/Windows with Ruff and unit tests
- [x] Create `ActivationEnergy/codu`, publish `v0.1.0`, and sync the formula to `ActivationEnergy/homebrew-cask`
- [ ] Add a real but redacted terminal screenshot or demo
- [ ] Confirm the repository contains no real Codex transcripts, credentials, or local-path data

## License

This project is licensed under the [MIT License](LICENSE).

## Related documentation

- [Codex App Server](https://developers.openai.com/codex/app-server/)
- [Codex projects and chats](https://learn.chatgpt.com/docs/projects)
