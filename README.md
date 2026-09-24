# codu

[简体中文](README.md) | [English](README.en.md)

一个面向本地 Codex 会话的终端浏览器。它借鉴 `ncdu` 和 `top` 的交互方式，让你快速查看会话的时间、ID、工作目录、名称或内容简介、状态和会话文件大小，并在确认后执行归档、恢复或删除。

> [!WARNING]
> 本项目目前处于实验阶段。删除是永久操作；使用前请先阅读[安全模型](#安全模型)与[已知限制](#已知限制)。

本项目不是 OpenAI 官方项目，也不隶属于 OpenAI。

## 功能

- `ncdu` 风格的全屏终端界面，支持方向键与 `j` / `k` 导航
- 显示 active/archived 状态、运行状态、更新时间、会话 ID、工作目录、名称/简介和 JSONL 文件大小
- 按状态、大小、更新时间、名称或工作目录正序/倒序排列
- 按名称、ID、目录或状态搜索
- 多选并批量归档、恢复或永久删除
- 删除确认支持 `A`（本次运行后续全部选择“是”）
- 从浏览器直接进入选中的 Codex 会话
- 支持 TSV 与 JSON 非交互输出
- 优先使用 Codex app-server；不可用时回退到本地会话文件扫描
- 仅依赖 Python 标准库

## 要求

| 项目 | 要求 |
| --- | --- |
| Python | 3.11 或更高版本 |
| Codex CLI | `codex` 命令在 `PATH` 中，并已完成正常配置 |
| 交互终端 | macOS 或 Linux 上支持 `curses` 的终端 |

Windows 的标准 Python 通常不自带 `curses`。此时仍可使用 `--plain` 和 `--json`；全屏交互界面目前未正式支持 Windows。

会话操作依赖 Codex CLI 提供的 `app-server`、`archive`、`unarchive`、`delete` 和 `resume` 命令。不同 Codex 版本的能力可能不同。

## 安装

直接运行仓库中的脚本：

```console
chmod +x codu
./codu
```

也可以安装到个人命令目录：

```console
mkdir -p ~/.local/bin
install -m 755 codu ~/.local/bin/codu
codu
```

如果 `~/.local/bin` 不在 `PATH` 中，请按你的 shell 配置将它加入 `PATH`。

### Homebrew

发布 `v0.1.0` 并将公式同步到 tap 后，可使用：

```console
brew tap ActivationEnergy/cask
brew install codu
```

Formula 源文件位于 [`packaging/homebrew/codu.rb`](packaging/homebrew/codu.rb)。它下载固定版本的单个 `codu`，校验 SHA256，并使用 Homebrew 的 Python 运行；不构建 Python 包。

## 快速开始

```console
# 浏览全部会话
codu

# 仅浏览工作目录完全匹配的会话
codu /path/to/project

# 当前目录
codu .

# 制表符分隔输出
codu --plain

# JSON 输出
codu /path/to/project --json
```

`directory` 可以是绝对路径、相对路径或包含 `~` 的路径。程序会先对它进行规范化，再与会话记录的工作目录精确匹配；不会自动包含子目录中的会话。

当 stdin 或 stdout 不是终端（例如接入管道）时，程序自动使用 `--plain` 输出。

## 命令行参数

```text
usage: codu [-h] [--json | --plain] [--verbose] [directory]
```

| 参数 | 说明 |
| --- | --- |
| `directory` | 可选。仅列出工作目录完全匹配的会话 |
| `--plain` | 输出制表符分隔列表，不进入全屏界面 |
| `--json` | 输出完整 JSON，包括字节数与会话文件路径 |
| `--verbose` | 在 stderr 显示 app-server 失败与文件扫描回退原因 |
| `-h`, `--help` | 显示帮助 |

## 交互快捷键

### 浏览

| 按键 | 操作 |
| --- | --- |
| `↑` / `↓`, `j` / `k` | 上下移动 |
| `PgUp` / `PgDn` | 翻页 |
| `Home` / `End`, `g` / `G` | 跳到首条/末条 |
| `Enter`, `→` | 查看会话详情 |
| `Esc`, `←` | 返回列表；在列表中清除搜索 |
| `/` | 搜索名称、ID、目录或状态 |
| `Space` | 选择或取消选择当前会话 |
| `?` | 打开帮助 |
| `q` | 返回或退出 |

### 排序

| 按键 | 排序列 |
| --- | --- |
| `1` | STATE |
| `2` | SIZE |
| `3` | UPDATED |
| `4` | NAME / INTRO |
| `5` | CWD |
| `<` / `>` | 切换到左侧/右侧排序列 |
| `o` | 切换正序 `↑` / 倒序 `↓` |

当前排序列与方向会同时显示在顶部状态栏和表头。

### 会话操作

| 按键 | 操作 |
| --- | --- |
| `a` | active 会话归档；archived 会话恢复 |
| `d` | 永久删除选中的会话 |
| `r` | 进入当前 Codex 会话；退出后返回浏览器 |

如果已有用 `Space` 标记的会话，`a` 和 `d` 会作用于**全部已标记会话**，而不是光标所在行。

删除确认：

- `y`：仅确认当前这次删除
- `N` 或其他按键：取消
- `A`：确认当前删除，并在本次 `codu` 运行期间自动确认后续删除

`A` 不会写入配置；退出程序后恢复逐次确认。

## 数据来源与工作方式

程序按以下顺序获取会话：

1. 通过 `codex app-server proxy` 连接已运行的共享 app-server。
2. 共享服务不可用时，临时启动 `codex app-server`。
3. app-server 接口不可用时，扫描 `$CODEX_HOME/sessions` 和 `$CODEX_HOME/archived_sessions`；`CODEX_HOME` 未设置时默认为 `~/.codex`。

app-server 路径使用 `thread/list` 的分页与目录筛选，并读取 Codex 返回的名称、简介、状态、时间和会话文件路径。兼容回退会读取 JSONL 元数据，并从 `session_index.jsonl` 获取 `/rename` 设置的名称。

| 环境变量 | 用途 |
| --- | --- |
| `CODEX_HOME` | 覆盖 Codex 数据目录；默认 `~/.codex` |
| `CODEX_BIN` | 覆盖 `codex` 可执行文件路径 |
| `CODU_TIMEOUT` | app-server 请求及删除/归档操作超时秒数；默认 `30` |

## 输出字段

| 字段 | 含义 |
| --- | --- |
| `state` / STATE | `active` 或 `archived` |
| `status` / STATUS | app-server 看到的运行状态；文件回退时为 `unknown` |
| `updated` / UPDATED | 最近更新时间，本地时区显示 |
| `session_id` / SESSION_ID | Codex 会话/线程 ID |
| `size_bytes` / SIZE | 当前会话 JSONL 文件大小 |
| `title` / NAME / INTRO | `/rename` 名称，或首条可识别的用户请求 |
| `cwd` / CWD | 会话记录的工作目录 |
| `path` | 会话 JSONL 文件路径，仅 JSON 输出 |

```console
codu --json | jq '.[] | select(.size_bytes > 1048576)'
codu --plain | column -t -s $'\t'
```

## 安全模型

- 浏览、筛选与导出不会直接修改会话日志；app-server 可能维护或修复自己的元数据索引。
- 归档与恢复通过 Codex CLI 执行，是可逆操作。
- 删除通过 `codex delete --force <UUID>` 执行，是永久操作。按 Codex 的线程删除语义，它还可能删除该线程的派生子线程。
- `STATUS=active...` 只表示**当前连接的 app-server**认为会话正在运行；`notLoaded` 或 `unknown` 不能证明其他 Codex 进程没有使用该会话。
- `A` 只在当前进程内关闭后续删除确认。启用后应特别留意当前选择与已标记会话。
- 程序通过参数数组调用 `codex`，不经过 shell 拼接会话 ID 或路径。

建议先归档不确定的会话，确认不再需要后再删除。

## 已知限制

- SIZE 仅统计当前线程对应的 JSONL 文件，不包含附件、生成图片、缓存，也不包含删除时可能一并移除的派生子线程；它不是精确的“可释放磁盘空间”。
- 新启动的本地 app-server 只能报告自身的运行状态，可能看不到其他 Codex 进程的活动。
- 文件扫描回退依赖 Codex 当前的本地 JSONL 格式；该内部格式变化时可能需要适配。
- 交互界面目前主要在 macOS 上验证；Windows 全屏模式尚未支持。
- app-server 请求及删除/归档操作默认 30 秒超时；`resume` 是交互操作，不应用该超时。

## 故障排查

### 没有显示任何会话

确认 Codex 已保存过会话，并检查：

```console
codex --version
codu --plain
```

如果设置了目录参数，请注意它是精确匹配；先省略目录确认会话是否存在。

### 全屏界面无法启动

```console
codu --plain
python3 -c 'import curses; print("curses OK")'
```

### 删除、归档或恢复失败

```console
codex delete --help
codex archive --help
codex unarchive --help
```

### 中文或宽字符显示错位

请使用 UTF-8 locale 和支持宽字符的终端字体。程序使用 Unicode East Asian Width 估算字符宽度，复杂 emoji 或部分组合字符仍可能错位。

## 开发与验证

```console
python3 -m unittest -v

# 可选
ruff check codu test_codu
```

当前测试覆盖会话发现、目录筛选、重命名与简介回退、时间解析、JSON 输出、app-server 字段映射与生命周期、请求超时、stderr 排空、归档后选择保持、搜索排序、宽字符裁剪以及删除确认按键。真实归档/删除和完整 curses 状态机仍需要更多自动化覆盖。

## 贡献

欢迎提交 issue 和 pull request。修复问题时请：

1. 说明可复现的 Codex CLI 版本、Python 版本和操作系统。
2. 为重要行为变更或 bug 修复添加回归测试。
3. 运行 `python3 -m unittest -v`；如果安装了 Ruff，再运行静态检查。
4. 不要在 issue 或测试夹具中提交真实会话内容、访问令牌或其他敏感信息。

## 开源发布前检查

- [x] 使用 MIT 许可证并在仓库根目录添加 `LICENSE`
- [x] 增加 CI（Python 3.11–3.14、Linux、Ruff 和单元测试）
- [ ] 创建 `ActivationEnergy/codu`，发布 `v0.1.0`，并将 Formula 同步到 `ActivationEnergy/homebrew-cask`
- [ ] 增加真实但已脱敏的终端截图或演示
- [ ] 确认仓库中没有真实 Codex 会话、凭据或本地路径数据

## 许可证

本项目采用 [MIT License](LICENSE)。

## 相关文档

- [Codex App Server](https://developers.openai.com/codex/app-server/)
- [Codex projects and chats](https://learn.chatgpt.com/docs/projects)
