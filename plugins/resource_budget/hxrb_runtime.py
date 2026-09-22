"""Hermes Resource Budget — best-effort terminal resource-policy guard.

This is intentionally a *policy plugin*, not a sandbox or process governor.
It uses Hermes' documented ``pre_tool_call`` hook to inspect only the native
``terminal`` tool and block recognized command invocations according to a
small, profile-scoped policy.  It also exposes a read-only host snapshot tool.

The default policy is deliberately low-friction:

* foreground/user-driven sessions are not blocked;
* unattended Hermes Kanban-lineage processes are blocked from recognized
  host-heavy terminal invocations;
* ordinary ``git clone`` is allowed unless the operator opts in;
* no service is restarted and no systemd/cgroup state is modified by the
  plugin itself.

The classifier is invocation-oriented rather than raw-substring-oriented: it
parses shell command positions, skips redirections and common wrappers, and
recursively inspects direct POSIX/Bash command/process substitutions (including
expanding unquoted heredocs while preserving quoted-heredoc inertness). It is
still finite and best-effort; it is not a complete shell parser or host security
boundary, and PowerShell/cmd semantics are not modeled.
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import shutil
import secrets
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

PLUGIN_ID = "hermesx-resource-budget"
PLUGIN_VERSION = "1.0.0"
VALID_MODES = {"observe", "block_unattended", "block_all", "pressure_only"}
SUPPORTED_TOOL_NAMES = {"terminal"}

DEFAULTS: dict[str, Any] = {
    "mode": "block_unattended",
    "min_home_fs_free_gb": 20,
    "max_load_per_cpu": 1.25,
    "max_cpu_psi_avg10": 25.0,
    "min_memory_available_gb": 2.0,
    "min_memory_available_percent": 5.0,
    "max_memory_psi_avg10": 20.0,
    "max_io_psi_avg10": 25.0,
    "protect_gateway_restart": True,
    "block_git_clone": False,
}

_SHELL_SEPARATORS = {";", "&&", "||", "|", "&", "\n", "(", ")"}
_REDIRECT_TOKENS = {"<", ">", "<<", ">>", "<<<", "<>", ">|", "<&", ">&"}
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", re.S)
_EXECUTABLE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_LARGE_FILE_RE = re.compile(
    r"(?i)(?:\.gguf|\.safetensors|\.iso|\.img|\.tar(?:\.gz|\.xz|\.zst)?|\.zip)(?:$|[?#&])"
)
_GATEWAY_UNIT_RE = re.compile(
    r"^hermes-gateway(?:@[A-Za-z0-9_.-]+|-[A-Za-z0-9_.-]+)?(?:\.service)?$",
    re.I,
)


def _safe_setting(ctx: Any, key: str, default: Any) -> Any:
    """Read the current profile's plugin setting, with older-Hermes fallback."""
    getter = getattr(ctx, "get_config", None)
    if callable(getter):
        try:
            return getter(key, default=default)
        except TypeError:
            try:
                return getter(key, default)
            except Exception:
                pass
        except Exception:
            pass
    return default


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _int(value: Any, default: int, *, minimum: int = 0, maximum: int = 1_000_000) -> int:
    # YAML booleans are integers in Python; treating ``true`` as 1 GiB is an
    # especially surprising policy mutation, so reject bool explicitly.
    if isinstance(value, bool):
        parsed = default
    else:
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError):
            parsed = default
    return min(max(parsed, minimum), maximum)


def _float(
    value: Any,
    default: float,
    *,
    minimum: float = 0.0,
    maximum: float = 1_000_000.0,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if not math.isfinite(parsed):
        parsed = float(default)
    return min(max(parsed, minimum), maximum)


def _policy(ctx: Any) -> dict[str, Any]:
    mode = str(_safe_setting(ctx, "mode", DEFAULTS["mode"]) or "").strip().lower()
    if mode not in VALID_MODES:
        mode = DEFAULTS["mode"]

    return {
        "mode": mode,
        "min_home_fs_free_gb": _int(
            _safe_setting(ctx, "min_home_fs_free_gb", DEFAULTS["min_home_fs_free_gb"]),
            DEFAULTS["min_home_fs_free_gb"],
            minimum=0,
        ),
        "max_load_per_cpu": _float(
            _safe_setting(ctx, "max_load_per_cpu", DEFAULTS["max_load_per_cpu"]),
            DEFAULTS["max_load_per_cpu"],
            minimum=0.0,
        ),
        "max_cpu_psi_avg10": _float(
            _safe_setting(ctx, "max_cpu_psi_avg10", DEFAULTS["max_cpu_psi_avg10"]),
            DEFAULTS["max_cpu_psi_avg10"],
            minimum=0.0,
        ),
        "min_memory_available_gb": _float(
            _safe_setting(
                ctx,
                "min_memory_available_gb",
                DEFAULTS["min_memory_available_gb"],
            ),
            DEFAULTS["min_memory_available_gb"],
            minimum=0.0,
        ),
        "min_memory_available_percent": _float(
            _safe_setting(
                ctx,
                "min_memory_available_percent",
                DEFAULTS["min_memory_available_percent"],
            ),
            DEFAULTS["min_memory_available_percent"],
            minimum=0.0,
            maximum=100.0,
        ),
        "max_memory_psi_avg10": _float(
            _safe_setting(
                ctx,
                "max_memory_psi_avg10",
                DEFAULTS["max_memory_psi_avg10"],
            ),
            DEFAULTS["max_memory_psi_avg10"],
            minimum=0.0,
        ),
        "max_io_psi_avg10": _float(
            _safe_setting(ctx, "max_io_psi_avg10", DEFAULTS["max_io_psi_avg10"]),
            DEFAULTS["max_io_psi_avg10"],
            minimum=0.0,
        ),
        "protect_gateway_restart": _bool(
            _safe_setting(
                ctx,
                "protect_gateway_restart",
                DEFAULTS["protect_gateway_restart"],
            ),
            DEFAULTS["protect_gateway_restart"],
        ),
        "block_git_clone": _bool(
            _safe_setting(ctx, "block_git_clone", DEFAULTS["block_git_clone"]),
            DEFAULTS["block_git_clone"],
        ),
    }


def _current_profile(ctx: Any, kwargs: Mapping[str, Any]) -> str:
    for key in ("profile_name", "profile"):
        value = kwargs.get(key)
        if value:
            return str(value)
    value = getattr(ctx, "profile_name", None)
    if value:
        return str(value)
    home = os.environ.get("HERMES_HOME", "")
    if "/profiles/" in home.replace("\\", "/"):
        return Path(home).name
    return "default"


def _is_unattended() -> bool:
    """Treat inherited Kanban lineage as unattended.

    This is intentionally broader than Hermes' internal dispatcher-owned-worker
    predicate.  Delegate/cron descendants that inherit HERMES_KANBAN_TASK stay
    in the conservative unattended bucket for this policy plugin.
    """
    return bool(os.environ.get("HERMES_KANBAN_TASK"))


def _event_id() -> str:
    """Return a random opaque event id; never derive telemetry ids from commands."""
    return secrets.token_hex(8)


def _strip_shell_comment(line: str) -> str:
    """Strip shell comments only when # starts an unquoted shell word."""
    quote: str | None = None
    escaped = False
    for idx, ch in enumerate(line):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in {"'", '"'}:
            quote = ch
            continue
        if ch == "#" and (idx == 0 or line[idx - 1].isspace() or line[idx - 1] in ";|&()"):
            return line[:idx]
    return line


def _line_heredoc_delimiters(line: str) -> list[tuple[str, bool, bool]]:
    """Return ``(delimiter, strip_tabs, expands)`` heredoc specs for one line.

    Bash expands parameter/command/arithmetic substitutions in an *unquoted*
    heredoc body. Any quoting in the delimiter word disables those expansions.
    We keep enough lexical information to distinguish those two cases while the
    outer command parser still discards heredoc body text itself.
    """
    text = str(line)
    result: list[tuple[str, bool, bool]] = []
    quote: str | None = None
    escaped = False
    idx = 0
    length = len(text)

    while idx < length:
        ch = text[idx]
        if escaped:
            escaped = False
            idx += 1
            continue
        if ch == "\\" and quote != "'":
            escaped = True
            idx += 1
            continue
        if quote:
            if ch == quote:
                quote = None
            idx += 1
            continue
        if ch in {"'", '"'}:
            quote = ch
            idx += 1
            continue

        if text.startswith("<<<", idx):
            idx += 3
            continue
        if not text.startswith("<<", idx):
            idx += 1
            continue

        idx += 2
        strip_tabs = False
        if idx < length and text[idx] == "-":
            strip_tabs = True
            idx += 1
        while idx < length and text[idx].isspace():
            idx += 1
        start = idx
        token_quote: str | None = None
        token_escaped = False
        while idx < length:
            c = text[idx]
            if token_escaped:
                token_escaped = False
                idx += 1
                continue
            if c == "\\" and token_quote != "'":
                token_escaped = True
                idx += 1
                continue
            if token_quote:
                if c == token_quote:
                    token_quote = None
                idx += 1
                continue
            if c in {"'", '"'}:
                token_quote = c
                idx += 1
                continue
            if c.isspace() or c in ";&|()<>":
                break
            idx += 1
        raw = text[start:idx]
        if not raw:
            continue
        expands = not any(c in raw for c in "'\"\\")
        try:
            parsed = shlex.split(raw, posix=True)
            delimiter = parsed[0] if parsed else ""
        except ValueError:
            delimiter = raw.strip("'\"")
        if delimiter:
            result.append((delimiter, strip_tabs, expands))

    return result


def _strip_nonexecuted_shell_text(script: str) -> str:
    """Remove heredoc bodies and shell comments before outer invocation parsing.

    Executable substitutions inside unquoted heredocs are extracted separately
    by :func:`_extract_executed_subcommands`; literal heredoc body words are data
    and must never be interpreted as commands.
    """
    output: list[str] = []
    pending: list[tuple[str, bool, bool]] = []

    for raw_line in str(script).splitlines():
        if pending:
            delimiter, strip_tabs, _expands = pending[0]
            candidate = raw_line.lstrip("\t") if strip_tabs else raw_line
            if candidate == delimiter:
                pending.pop(0)
            output.append("")
            continue

        line = _strip_shell_comment(raw_line)
        output.append(line)
        pending.extend(_line_heredoc_delimiters(line))

    return "\n".join(output)


def _balanced_shell_payload(text: str, start: int) -> tuple[str | None, int]:
    """Read a parenthesized shell payload beginning just after ``(``."""
    depth = 1
    quote: str | None = None
    escaped = False
    idx = start
    payload_start = start

    while idx < len(text):
        ch = text[idx]
        if escaped:
            escaped = False
            idx += 1
            continue
        if ch == "\\" and quote != "'":
            escaped = True
            idx += 1
            continue
        if quote:
            if ch == quote:
                quote = None
            idx += 1
            continue
        if ch in {"'", '"'}:
            quote = ch
            idx += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[payload_start:idx], idx + 1
        idx += 1
    return None, len(text)


def _backtick_payload(text: str, start: int) -> tuple[str | None, int]:
    escaped = False
    idx = start
    out: list[str] = []
    while idx < len(text):
        ch = text[idx]
        if escaped:
            out.append(ch)
            escaped = False
            idx += 1
            continue
        if ch == "\\":
            escaped = True
            idx += 1
            continue
        if ch == "`":
            return "".join(out), idx + 1
        out.append(ch)
        idx += 1
    return None, len(text)


def _extract_expansions_from_text(text: str) -> list[str]:
    """Extract shell constructs that execute nested commands.

    Supported direct Bash/POSIX execution forms: ``$(...)``, legacy backticks,
    and process substitutions ``<(...)`` / ``>(...)``. Single-quoted text is
    inert; command substitution remains active inside double quotes; process
    substitution does not.
    """
    result: list[str] = []
    quote: str | None = None
    escaped = False
    idx = 0

    while idx < len(text):
        ch = text[idx]
        if escaped:
            escaped = False
            idx += 1
            continue
        if ch == "\\" and quote != "'":
            escaped = True
            idx += 1
            continue
        if quote == "'":
            if ch == "'":
                quote = None
            idx += 1
            continue
        if ch == "'" and quote is None:
            quote = "'"
            idx += 1
            continue
        if ch == '"':
            quote = None if quote == '"' else ('"' if quote is None else quote)
            idx += 1
            continue

        if text.startswith("$(", idx):
            payload, end = _balanced_shell_payload(text, idx + 2)
            if payload is not None:
                result.append(payload)
            idx = end
            continue

        if quote is None and idx + 1 < len(text) and ch in "<>" and text[idx + 1] == "(":
            payload, end = _balanced_shell_payload(text, idx + 2)
            if payload is not None:
                result.append(payload)
            idx = end
            continue

        if ch == "`":
            payload, end = _backtick_payload(text, idx + 1)
            if payload is not None:
                result.append(payload)
            idx = end
            continue

        idx += 1

    return result


def _extract_executed_subcommands(script: str) -> list[str]:
    """Extract nested commands executed as part of *script* evaluation.

    Unquoted heredoc bodies are scanned only for executable substitutions;
    quoted heredoc bodies are completely inert. Literal heredoc contents are
    never fed to the normal command-position parser.
    """
    result: list[str] = []
    pending: list[tuple[str, bool, bool]] = []

    for raw_line in str(script).splitlines():
        if pending:
            delimiter, strip_tabs, expands = pending[0]
            candidate = raw_line.lstrip("\t") if strip_tabs else raw_line
            if candidate == delimiter:
                pending.pop(0)
                continue
            if expands:
                result.extend(_extract_expansions_from_text(raw_line))
            continue

        line = _strip_shell_comment(raw_line)
        result.extend(_extract_expansions_from_text(line))
        pending.extend(_line_heredoc_delimiters(line))

    return result


def _shell_segments(script: str) -> list[list[str]]:
    cleaned = _strip_nonexecuted_shell_text(script)
    try:
        lexer = shlex.shlex(cleaned, posix=True, punctuation_chars=";&|()<>\n")
        # Preserve newline as a command separator token.
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except (ValueError, TypeError):
        return []

    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _SHELL_SEPARATORS or (
            token and set(token) <= set(";&|()\n")
        ):
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _without_redirections(tokens: Sequence[str]) -> list[str]:
    result: list[str] = []
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if (
            token.isdigit()
            and idx + 1 < len(tokens)
            and tokens[idx + 1] in _REDIRECT_TOKENS
        ):
            idx += 2
            if idx < len(tokens):
                idx += 1
            continue
        if token in _REDIRECT_TOKENS:
            idx += 1
            if idx < len(tokens):
                idx += 1
            continue
        result.append(token)
        idx += 1
    return result


def _basename(value: str) -> str:
    return value.replace("\\", "/").rsplit("/", 1)[-1].lower()


def _skip_options(
    argv: Sequence[str],
    start: int,
    *,
    value_options: Iterable[str] = (),
) -> int:
    value_options_set = set(value_options)
    idx = start
    while idx < len(argv):
        arg = argv[idx]
        if arg == "--":
            return idx + 1
        if not arg.startswith("-") or arg == "-":
            return idx
        name = arg.split("=", 1)[0]
        if name in value_options_set and "=" not in arg:
            idx += 2
        else:
            idx += 1
    return idx


def _unwrap_common_wrappers(tokens: Sequence[str]) -> list[str]:
    argv = list(tokens)

    # Shell control words / brace groups that can prefix a real command in the same segment.
    while argv and argv[0].lower() in {"if", "then", "elif", "else", "while", "until", "do", "!", "{"}:
        argv.pop(0)
    while argv and argv[-1] == "}":
        argv.pop()

    # Variable assignments may precede a command.
    while argv and _ASSIGNMENT_RE.match(argv[0]):
        argv.pop(0)

    for _ in range(8):
        if not argv:
            return []
        exe = _basename(argv[0])

        if exe in {"command", "exec", "nohup", "time"}:
            idx = _skip_options(argv, 1)
            argv = argv[idx:]
            continue

        if exe == "sudo":
            idx = _skip_options(
                argv,
                1,
                value_options={
                    "-u", "--user", "-g", "--group", "-h", "--host",
                    "-p", "--prompt", "-C", "--close-from", "-D", "--chdir",
                    "-R", "--chroot", "-T", "--command-timeout",
                },
            )
            argv = argv[idx:]
            while argv and _ASSIGNMENT_RE.match(argv[0]):
                argv.pop(0)
            continue

        if exe == "env":
            # GNU env -S/--split-string asks env itself to split one argument
            # into a command line.  Follow that execution boundary rather than
            # treating the split string as inert data.
            idx = 1
            split_payload: str | None = None
            while idx < len(argv):
                arg = argv[idx]
                if arg == "--":
                    idx += 1
                    break
                if arg in {"-S", "--split-string"}:
                    if idx + 1 >= len(argv):
                        return []
                    split_payload = argv[idx + 1]
                    idx += 2
                    break
                if arg.startswith("--split-string="):
                    split_payload = arg.split("=", 1)[1]
                    idx += 1
                    break
                if arg in {"-u", "--unset", "-C", "--chdir", "-a", "--argv0"}:
                    idx += 2
                    continue
                if any(arg.startswith(prefix + "=") for prefix in {"--unset", "--chdir", "--argv0"}):
                    idx += 1
                    continue
                if arg.startswith("-") and arg != "-":
                    idx += 1
                    continue
                break

            if split_payload is not None:
                try:
                    split_argv = shlex.split(split_payload, posix=True)
                except ValueError:
                    return []
                argv = split_argv + argv[idx:]
            else:
                argv = argv[idx:]
            while argv and _ASSIGNMENT_RE.match(argv[0]):
                argv.pop(0)
            continue

        if exe == "nice":
            idx = _skip_options(argv, 1, value_options={"-n", "--adjustment"})
            argv = argv[idx:]
            continue

        if exe == "ionice":
            idx = _skip_options(
                argv,
                1,
                value_options={"-c", "--class", "-n", "--classdata", "-t", "--ignore"},
            )
            argv = argv[idx:]
            continue

        if exe == "timeout":
            idx = _skip_options(
                argv,
                1,
                value_options={"-k", "--kill-after", "-s", "--signal"},
            )
            if idx < len(argv):
                idx += 1  # duration
            argv = argv[idx:]
            continue

        if exe == "taskset":
            idx = _skip_options(argv, 1, value_options={"-p", "--pid", "-c", "--cpu-list"})
            argv = argv[idx:]
            continue

        break

    return argv


def _first_action_after_options(
    argv: Sequence[str],
    start: int,
    *,
    value_options: Iterable[str] = (),
) -> tuple[str | None, int]:
    idx = _skip_options(argv, start, value_options=value_options)
    if idx >= len(argv):
        return None, idx
    return argv[idx].lower(), idx


def _docker_reason(argv: Sequence[str]) -> str | None:
    global_value_options = {
        "-H", "--host", "--context", "--config", "-l", "--log-level",
        "--tlscacert", "--tlscert", "--tlskey",
    }
    action, idx = _first_action_after_options(argv, 1, value_options=global_value_options)
    if action is None:
        return None

    # Only *action-level* version/help forms are informational.  A positional
    # image/service/build-context literally named "version" is still real work.
    if action in {"version", "help"}:
        return None
    if "--help" in argv[idx + 1 :] or "-h" in argv[idx + 1 :]:
        return None

    if action in {"pull", "build"}:
        return "container image download/build"
    if action == "image":
        nested, nested_idx = _first_action_after_options(argv, idx + 1)
        if nested in {"help", "version"}:
            return None
        if nested and ("--help" in argv[nested_idx + 1 :] or "-h" in argv[nested_idx + 1 :]):
            return None
        if nested in {"pull", "build"}:
            return "container image download/build"
    if action == "buildx":
        nested, nested_idx = _first_action_after_options(argv, idx + 1)
        if nested and ("--help" in argv[nested_idx + 1 :] or "-h" in argv[nested_idx + 1 :]):
            return None
        if nested == "build":
            return "container image download/build"
    if action == "compose":
        compose_value_options = {
            "-f", "--file", "-p", "--project-name", "--profile", "--env-file",
            "--project-directory", "--ansi", "--progress", "--parallel",
        }
        nested, nested_idx = _first_action_after_options(
            argv, idx + 1, value_options=compose_value_options
        )
        if nested and ("--help" in argv[nested_idx + 1 :] or "-h" in argv[nested_idx + 1 :]):
            return None
        if nested == "build":
            return "container compose build"
    return None


def _git_reason(argv: Sequence[str], policy: Mapping[str, Any]) -> str | None:
    git_value_options = {
        "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
        "--super-prefix", "--config-env",
    }
    action, idx = _first_action_after_options(argv, 1, value_options=git_value_options)
    if action is None:
        return None

    # git --help <cmd>, git help <cmd>, and <cmd> --help are informational.
    if action == "help" or "--help" in argv[1:] or "-h" in argv[1:]:
        return None
    if action == "clone" and policy.get("block_git_clone"):
        return "repository clone with unknown transfer size"
    if action != "lfs":
        return None
    lfs_action, lfs_idx = _first_action_after_options(argv, idx + 1)
    if lfs_action not in {"pull", "fetch"}:
        return None
    tail = list(argv[lfs_idx + 1 :])
    if "--help" in tail or "-h" in tail:
        return None
    if lfs_action == "fetch" and any(arg in {"--dry-run", "-d"} for arg in tail):
        return None
    return "Git LFS bulk transfer"


def _package_manager_reason(exe: str, argv: Sequence[str]) -> str | None:
    lower = [str(arg).lower() for arg in argv]
    if any(arg in {"--help", "-h"} for arg in lower[1:]):
        return None

    if exe in {"apt", "apt-get"}:
        # apt/apt-get simulation modes perform no package transaction.
        if any(arg in {"-s", "--simulate", "--just-print", "--dry-run", "--no-act"} for arg in lower[1:]):
            return None
        if any(arg.startswith("-") and not arg.startswith("--") and "s" in arg[1:] for arg in lower[1:]):
            return None
        action, _ = _first_action_after_options(
            argv,
            1,
            value_options={"-o", "--option", "-t", "--target-release", "-c", "--config-file"},
        )
        if action in {"upgrade", "dist-upgrade", "full-upgrade"}:
            return "bulk operating-system package transaction"
        return None

    if exe in {"dnf", "yum", "zypper"}:
        # zypper uses uppercase -D for dry-run; preserve case for the short form.
        if exe == "zypper" and any(arg == "--dry-run" or arg == "-D" for arg in argv[1:]):
            return None
        action, _ = _first_action_after_options(
            argv,
            1,
            value_options={"--releasever", "--installroot", "--setopt", "--config", "-c"},
        )
        valid_actions = {
            "upgrade", "update", "upgrade-minimal", "distribution-synchronization",
            "distrosync", "distro-sync",
        }
        if exe == "zypper":
            valid_actions = {"update", "up", "dup", "dist-upgrade", "upgrade"}
        if action in valid_actions:
            return "bulk operating-system package transaction"
        return None

    if exe == "pacman":
        sync = False
        sysupgrade = False
        for arg in argv[1:]:
            if arg == "--sync" or arg.startswith("--sync="):
                sync = True
            elif arg == "--sysupgrade" or arg.startswith("--sysupgrade="):
                sysupgrade = True
            elif arg.startswith("-") and not arg.startswith("--") and arg != "-":
                flags = arg[1:]
                sync = sync or "S" in flags
                sysupgrade = sysupgrade or "u" in flags
        if sync and sysupgrade:
            return "bulk operating-system package transaction"
    return None


def _systemctl_gateway_reason(argv: Sequence[str]) -> str | None:
    action, idx = _first_action_after_options(
        argv,
        1,
        value_options={"--root", "--runtime-scope", "--machine", "-M", "--type", "-t"},
    )
    if action not in {"restart", "try-restart", "reload-or-restart", "try-reload-or-restart", "stop", "disable", "mask"}:
        return None
    for unit in argv[idx + 1 :]:
        unit_name = Path(unit).name
        if unit_name.lower().endswith((".timer", ".socket", ".target", ".path", ".mount", ".slice", ".scope")):
            continue
        if _GATEWAY_UNIT_RE.match(unit_name):
            return "Hermes gateway lifecycle mutation"
    return None


def _hermes_gateway_reason(argv: Sequence[str]) -> str | None:
    idx = 1
    while idx < len(argv):
        arg = argv[idx]
        if arg in {"-p", "--profile"}:
            idx += 2
            continue
        if arg.startswith("--profile="):
            idx += 1
            continue
        if arg.startswith("-"):
            idx += 1
            continue
        break
    if idx + 1 < len(argv) and argv[idx].lower() == "gateway":
        if argv[idx + 1].lower() in {"restart", "stop"}:
            return "Hermes gateway lifecycle mutation"
    return None


def _hermes_profile_roots() -> list[Path]:
    roots: list[Path] = []
    env_home = os.environ.get("HERMES_HOME")
    if env_home:
        path = Path(env_home).expanduser()
        parts = path.parts
        if "profiles" in parts:
            try:
                idx = parts.index("profiles")
                roots.append(Path(*parts[: idx + 1]))
            except Exception:
                pass
        else:
            roots.append(path / "profiles")
    roots.append(Path.home() / ".hermes" / "profiles")

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def _known_profile_names() -> set[str]:
    """Return profile names proven by an on-disk Hermes profile directory."""
    names: set[str] = set()
    for root in _hermes_profile_roots():
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_dir():
                    continue
                # Require a Hermes-ish marker rather than trusting directory shape alone.
                if not any((entry / marker).exists() for marker in ("config.yaml", ".env", "SOUL.md")):
                    continue
            except OSError:
                continue
            names.add(entry.name.lower())
    return names


def _profile_wrapper_gateway_reason(argv: Sequence[str]) -> str | None:
    if len(argv) < 3:
        return None
    wrapper = _basename(argv[0])
    if wrapper in {"hermes", "systemctl"} or not _EXECUTABLE_NAME_RE.match(wrapper):
        return None
    if wrapper not in _known_profile_names():
        return None
    if argv[1].lower() == "gateway" and argv[2].lower() in {"restart", "stop"}:
        return "Hermes profile gateway lifecycle mutation"
    return None


def _shell_launcher_payload(argv: Sequence[str]) -> str | None:
    """Return the command-string passed to a POSIX-family shell ``-c``.

    Parse shell options structurally: long options such as ``--norc`` and
    ``--rcfile`` must never be mistaken for ``-c`` merely because they contain
    the letter c.  Bash also accepts an option terminator between ``-c`` and
    the command string (``bash -c -- 'cmd'``).
    """
    if not argv:
        return None
    exe = _basename(argv[0])
    if exe not in {"sh", "bash", "dash", "zsh", "ksh", "fish"}:
        return None

    long_value_options = {"--rcfile", "--init-file"}
    idx = 1
    while idx < len(argv):
        arg = argv[idx]
        if arg == "--":
            return None  # no -c was seen; following token is a script/operand
        if arg in long_value_options:
            idx += 2
            continue
        if any(arg.startswith(opt + "=") for opt in long_value_options):
            idx += 1
            continue
        if arg.startswith("--"):
            idx += 1
            continue
        if arg.startswith("-") and arg != "-":
            flags = arg[1:]
            if "c" in flags:
                payload_idx = idx + 1
                if payload_idx < len(argv) and argv[payload_idx] == "--":
                    payload_idx += 1
                return argv[payload_idx] if payload_idx < len(argv) else None
            idx += 1
            continue
        # First non-option without -c is a script filename/operand.
        return None
    return None


def _classify_invocation(argv: Sequence[str], policy: Mapping[str, Any], *, depth: int) -> str | None:
    argv = _unwrap_common_wrappers(argv)
    if not argv:
        return None

    nested = _shell_launcher_payload(argv)
    if nested is not None and depth < 3:
        return _classify(nested, policy, _depth=depth + 1)

    exe = _basename(argv[0])

    if policy.get("protect_gateway_restart"):
        if exe == "hermes":
            reason = _hermes_gateway_reason(argv)
            if reason:
                return reason
        elif exe == "systemctl":
            reason = _systemctl_gateway_reason(argv)
            if reason:
                return reason
        else:
            reason = _profile_wrapper_gateway_reason(argv)
            if reason:
                return reason

    if exe in {"docker", "podman"}:
        return _docker_reason(argv)

    if exe in {"docker-compose", "podman-compose"}:
        if any(arg in {"--help", "-h", "--version"} for arg in argv[1:]):
            return None
        action, _ = _first_action_after_options(
            argv,
            1,
            value_options={"-f", "--file", "-p", "--project-name", "--env-file"},
        )
        if action == "build":
            return "container compose build"
        return None

    if exe == "git":
        return _git_reason(argv, policy)

    if exe == "ollama":
        if any(arg in {"--help", "-h", "--version"} for arg in argv[1:]):
            return None
        action, _ = _first_action_after_options(argv, 1)
        if action in {"pull", "run", "create"}:
            return "Ollama model download/build/inference"
        return None

    if exe in {"hf", "huggingface-cli"}:
        if any(arg in {"--help", "-h", "--version"} for arg in argv[1:]):
            return None
        action, _ = _first_action_after_options(argv, 1)
        if action == "download":
            return "model/data bulk download"
        return None

    if exe in {"fio", "stress", "stress-ng"}:
        if not any(arg in {"-h", "--help", "--version", "-V"} for arg in argv[1:]):
            return "host stress/benchmark"
        return None

    if exe == "sysbench":
        if any(arg.lower() == "run" for arg in argv[1:]):
            return "host stress/benchmark"
        return None

    if exe == "dd":
        for arg in argv[1:]:
            lowered = arg.lower()
            if lowered.startswith("if=/dev/") and lowered.split("=", 1)[1] in {"/dev/zero", "/dev/urandom"}:
                return "bulk/raw disk I/O"
            if lowered.startswith("of=/dev/"):
                return "bulk/raw disk I/O"
        return None

    if exe in {"curl", "wget", "aria2c"}:
        if any(_LARGE_FILE_RE.search(str(arg)) for arg in argv[1:]):
            return "potentially large binary/archive download"
        return None

    package_reason = _package_manager_reason(exe, argv)
    if package_reason:
        return package_reason

    return None


def _classify(command: str, policy: Mapping[str, Any], *, _depth: int = 0) -> str | None:
    """Classify recognized executed command positions, including direct shell expansions."""
    if _depth < 6:
        for nested in _extract_executed_subcommands(command):
            reason = _classify(nested, policy, _depth=_depth + 1)
            if reason:
                return reason

    for raw_segment in _shell_segments(command):
        segment = _without_redirections(raw_segment)
        reason = _classify_invocation(segment, policy, depth=_depth)
        if reason:
            return reason
    return None


def _parse_linux_meminfo(text: str) -> dict[str, float | None]:
    """Parse aggregate Linux memory/swap counters without enumerating processes."""
    values: dict[str, int] = {}
    for line in str(text).splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        if key not in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            continue
        parts = raw.strip().split()
        if not parts:
            continue
        try:
            values[key] = int(parts[0])
        except (TypeError, ValueError):
            continue

    total_kib = values.get("MemTotal")
    available_kib = values.get("MemAvailable")
    swap_total_kib = values.get("SwapTotal")
    swap_free_kib = values.get("SwapFree")

    def gib(kib: int | None) -> float | None:
        if kib is None:
            return None
        return round(kib / 1024 / 1024, 3)

    memory_available_percent = None
    if total_kib and available_kib is not None:
        memory_available_percent = round((available_kib / total_kib) * 100.0, 3)

    swap_used_kib = None
    swap_used_percent = None
    if swap_total_kib is not None and swap_free_kib is not None:
        swap_used_kib = max(0, swap_total_kib - swap_free_kib)
        swap_used_percent = (
            round((swap_used_kib / swap_total_kib) * 100.0, 3)
            if swap_total_kib > 0
            else 0.0
        )

    return {
        "memory_total_gb": gib(total_kib),
        "memory_available_gb": gib(available_kib),
        "memory_available_percent": memory_available_percent,
        "swap_total_gb": gib(swap_total_kib),
        "swap_free_gb": gib(swap_free_kib),
        "swap_used_gb": gib(swap_used_kib),
        "swap_used_percent": swap_used_percent,
    }


def _linux_memory_snapshot() -> dict[str, float | None]:
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    return _parse_linux_meminfo(text)


def _parse_psi_avg10(text: str) -> float | None:
    """Return Linux PSI ``some avg10`` from one aggregate pressure file."""
    for line in str(text).splitlines():
        if not line.startswith("some "):
            continue
        match = re.search(r"\bavg10=([0-9.]+)", line)
        if match:
            try:
                value = float(match.group(1))
            except ValueError:
                return None
            return value if math.isfinite(value) else None
    return None


def _linux_psi_avg10(resource: str) -> float | None:
    if resource not in {"cpu", "memory", "io"}:
        return None
    try:
        text = Path(f"/proc/pressure/{resource}").read_text(
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return None
    return _parse_psi_avg10(text)


def _host_snapshot() -> dict[str, Any]:
    cpus = max(1, int(os.cpu_count() or 1))
    try:
        load1, load5, load15 = os.getloadavg()
    except (AttributeError, OSError):
        load1 = load5 = load15 = None

    try:
        disk = shutil.disk_usage(Path.home())
        home_fs_disk_free_gb = round(disk.free / (1024**3), 3)
        home_fs_disk_free_percent = (
            round((disk.free / disk.total) * 100.0, 3) if disk.total else None
        )
    except OSError:
        home_fs_disk_free_gb = None
        home_fs_disk_free_percent = None

    memory = _linux_memory_snapshot()
    return {
        "logical_cpus": cpus,
        "load1": load1,
        "load5": load5,
        "load15": load15,
        "load_per_cpu": round(load1 / cpus, 4) if load1 is not None else None,
        "cpu_psi_avg10": _linux_psi_avg10("cpu"),
        **memory,
        "home_fs_disk_free_gb": home_fs_disk_free_gb,
        "home_fs_disk_free_percent": home_fs_disk_free_percent,
        "io_psi_avg10": _linux_psi_avg10("io"),
        "memory_psi_avg10": _linux_psi_avg10("memory"),
    }


def _headroom(snapshot: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    """Build an explainable host-headroom vector; no weighted/magic score."""
    domains: dict[str, dict[str, Any]] = {
        "cpu": {"pressured": False, "reasons": []},
        "memory": {"pressured": False, "reasons": []},
        "io": {"pressured": False, "reasons": []},
        "disk": {"pressured": False, "reasons": []},
    }

    def add(domain: str, reason: str) -> None:
        domains[domain]["pressured"] = True
        domains[domain]["reasons"].append(reason)

    disk = snapshot.get("home_fs_disk_free_gb")
    min_disk = policy["min_home_fs_free_gb"]
    if min_disk > 0 and isinstance(disk, (int, float)) and disk < min_disk:
        add("disk", f"home filesystem free {disk:.1f} GiB < {min_disk} GiB threshold")

    load = snapshot.get("load_per_cpu")
    max_load = policy["max_load_per_cpu"]
    if max_load > 0 and isinstance(load, (int, float)) and load > max_load:
        add("cpu", f"load/CPU {load:.2f} > {max_load:.2f} threshold")

    cpu_psi = snapshot.get("cpu_psi_avg10")
    max_cpu_psi = policy["max_cpu_psi_avg10"]
    if max_cpu_psi > 0 and isinstance(cpu_psi, (int, float)) and cpu_psi > max_cpu_psi:
        add("cpu", f"CPU PSI avg10 {cpu_psi:.2f} > {max_cpu_psi:.2f} threshold")

    memory_gb = snapshot.get("memory_available_gb")
    min_memory_gb = policy["min_memory_available_gb"]
    if min_memory_gb > 0 and isinstance(memory_gb, (int, float)) and memory_gb < min_memory_gb:
        add("memory", f"memory available {memory_gb:.1f} GiB < {min_memory_gb:.1f} GiB threshold")

    memory_percent = snapshot.get("memory_available_percent")
    min_memory_percent = policy["min_memory_available_percent"]
    if (
        min_memory_percent > 0
        and isinstance(memory_percent, (int, float))
        and memory_percent < min_memory_percent
    ):
        add(
            "memory",
            f"memory available {memory_percent:.1f}% < {min_memory_percent:.1f}% threshold",
        )

    memory_psi = snapshot.get("memory_psi_avg10")
    max_memory_psi = policy["max_memory_psi_avg10"]
    if (
        max_memory_psi > 0
        and isinstance(memory_psi, (int, float))
        and memory_psi > max_memory_psi
    ):
        add(
            "memory",
            f"memory PSI avg10 {memory_psi:.2f} > {max_memory_psi:.2f} threshold",
        )

    io_psi = snapshot.get("io_psi_avg10")
    max_io_psi = policy["max_io_psi_avg10"]
    if max_io_psi > 0 and isinstance(io_psi, (int, float)) and io_psi > max_io_psi:
        add("io", f"IO PSI avg10 {io_psi:.2f} > {max_io_psi:.2f} threshold")

    reasons = [reason for domain in domains.values() for reason in domain["reasons"]]
    return {
        "pressured": bool(reasons),
        "domains": domains,
        "reasons": reasons,
        "score": None,
        "score_note": "No composite score: policy uses explicit configured thresholds.",
    }


def _pressure(snapshot: Mapping[str, Any], policy: Mapping[str, Any]) -> list[str]:
    return list(_headroom(snapshot, policy)["reasons"])


def _record(ctx: Any, *, blocked: bool, reason: str, event_id: str, tool_name: str) -> None:
    """Best-effort command-content-free telemetry.

    Hermes currently exposes atomic individual state get/set operations, not an
    atomic increment primitive.  Counts are therefore explicitly approximate
    under multi-process concurrency; enforcement never depends on them.
    """
    state = getattr(ctx, "state", None)
    if state is None:
        return
    try:
        key = "blocked_count_approx" if blocked else "observed_count_approx"
        previous = state.get(key, default=0)
        try:
            count = int(previous or 0) + 1
        except (TypeError, ValueError):
            count = 1
        state.set(key, count)
        state.set(
            "last_event",
            {
                "ts": time.time(),
                "blocked": blocked,
                "reason": reason,
                "event_id": event_id,
                "tool": str(tool_name or ""),
            },
        )
    except Exception:
        # Telemetry must never become a new failure mode.
        pass


class _Runtime:
    def __init__(self, ctx: Any):
        self.ctx = ctx

    def pre_tool_call(self, tool_name: str = "", args: Any = None, **kwargs: Any):
        if not isinstance(args, dict):
            return None
        if str(tool_name or "") not in SUPPORTED_TOOL_NAMES:
            return None

        command = str(args.get("command") or "")
        if not command:
            return None

        policy = _policy(self.ctx)
        reason = _classify(command, policy)
        if not reason:
            return None

        mode = policy["mode"]
        unattended = _is_unattended()
        snapshot = _host_snapshot()
        pressure = _pressure(snapshot, policy)

        should_block = False
        if mode == "block_all":
            should_block = True
        elif mode == "block_unattended":
            should_block = unattended
        elif mode == "pressure_only":
            should_block = bool(pressure)
        elif mode == "observe":
            should_block = False

        event_id = _event_id()
        _record(
            self.ctx,
            blocked=should_block,
            reason=reason,
            event_id=event_id,
            tool_name=str(tool_name or ""),
        )

        if not should_block:
            return None

        profile = _current_profile(self.ctx, kwargs)
        pressure_text = f" Host pressure: {'; '.join(pressure)}." if pressure else ""
        return {
            "action": "block",
            "message": (
                f"Hermes Resource Budget blocked a recognized terminal operation ({reason}). "
                f"mode={mode} unattended={str(unattended).lower()} profile={profile!r}."
                f"{pressure_text} event_id={event_id}. "
                "This is a categorical best-effort terminal policy rule, not a measured "
                "transfer/runtime threshold or host sandbox. No command contents or "
                "credentials were persisted. Run the operation from an operator-controlled "
                "foreground workflow, or deliberately change this profile's plugin mode "
                "if that is intended."
            ),
        }

    def status(self, params: Any = None, **kwargs: Any) -> str:
        del params, kwargs
        policy = _policy(self.ctx)
        snapshot = _host_snapshot()
        headroom = _headroom(snapshot, policy)
        pressure = list(headroom["reasons"])

        state_summary: dict[str, Any] = {}
        state = getattr(self.ctx, "state", None)
        if state is not None:
            for key in ("blocked_count_approx", "observed_count_approx", "last_event"):
                try:
                    value = state.get(key, default=None)
                except Exception:
                    continue
                if value is not None:
                    state_summary[key] = value

        return json.dumps(
            {
                "plugin": PLUGIN_ID,
                "version": PLUGIN_VERSION,
                "profile": _current_profile(self.ctx, {}),
                "unattended": _is_unattended(),
                "policy": policy,
                "host": snapshot,
                "headroom": headroom,
                "pressure": pressure,
                "privacy": {
                    "aggregate_host_signals_only": True,
                    "process_enumeration": False,
                    "network_inspection": False,
                    "gpu_or_thermal_probe": False,
                    "background_sampling": False,
                    "command_contents_persisted": False,
                },
                "state": state_summary,
                "telemetry": {
                    "counts_are_approximate": True,
                    "reason": (
                        "Hermes plugin state exposes atomic get/set operations but no atomic "
                        "increment; concurrent processes can lose counter increments."
                    ),
                    "enforcement_depends_on_counts": False,
                },
                "enforcement": {
                    "tool_gate": True,
                    "supported_tool_names": sorted(SUPPORTED_TOOL_NAMES),
                    "best_effort_terminal_classifier": True,
                    "sandbox": False,
                    "hard_cpu_cap": False,
                    "hard_memory_cap": False,
                    "hard_gpu_cap": False,
                    "hard_disk_io_cap": False,
                    "hard_network_cap": False,
                    "shell_scope": "POSIX/Bash-oriented command grammar; PowerShell/cmd semantics are not modeled",
                    "note": (
                        "Best-effort native terminal pre_tool_call policy gate only; it does "
                        "not cover every possible execution surface and does not install a "
                        "systemd/cgroup governor."
                    ),
                },
            },
            indent=2,
            sort_keys=True,
            default=str,
        )


_STATUS_SCHEMA = {
    "name": "resource_budget_status",
    "description": (
        "Read the active Hermes Resource Budget policy, transparent CPU/memory/I/O/disk "
        "headroom vector, aggregate host snapshot, and command-content-free approximate "
        "gate statistics."
    ),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}


def register(ctx: Any) -> None:
    runtime = _Runtime(ctx)
    ctx.register_hook("pre_tool_call", runtime.pre_tool_call)
    ctx.register_tool(
        name="resource_budget_status",
        toolset="resource_budget",
        schema=_STATUS_SCHEMA,
        handler=runtime.status,
        description=_STATUS_SCHEMA["description"],
        emoji="🧯",
    )
