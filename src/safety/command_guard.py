"""Pre-flight checks for shell commands before a terminal round-trip.

Catches obvious breakage (missing binary, macOS-only `open` on Linux,
`xdg-open` of a nonexistent file) instantly with an actionable message,
so the ReAct loop repairs from information instead of blind retries.

P1.1 adds a small shell AST (CommandNode / Pipe / Redir / Subshell / Chain)
built from an operator-aware tokenizer, so later steps can score risk and
enforce verdicts without a direct `bash -lc` bypass.
"""

from dataclasses import dataclass, field
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from auto_system_agent.platforms.os_utils import REMOVE_VERBS, UNINSTALL_MANAGERS, detect_os


# ---------------------------------------------------------------------------
# P1.1a: shell AST nodes + operator-aware tokenizer + simple-command parser.
# Compound parsing (Chain/Pipe/Redir/Subshell) is extended in P1.1b, and
# expansion/glob detection lives in P1.1c.
# ---------------------------------------------------------------------------

#: Separators that join independent commands.
CHAIN_SEPARATORS = (";", "&&", "||")

#: Operators treated as redirections (prefix number like `2>` kept together).
REDIR_OPERATORS = (">>", "<<", "2>>", "2>", "&>", ">", "<")

_ALL_OPERATORS = ("&&", "||", ">>", "<<", "2>>", "2>", "&>", "|", ";", ">", "<", "(", ")")


class CommandSyntaxError(ValueError):
    """Raised when shell text cannot be parsed (unclosed quote, bad redir)."""


@dataclass
class CommandNode:
    """A single simple command: leading VAR= assignments + argv."""

    argv: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class PipeNode:
    """`left | right` pipeline (right may nest further pipes)."""

    left: object
    right: object


@dataclass
class RedirNode:
    """A command with one shell redirection applied."""

    cmd: object
    target: str
    op: str


@dataclass
class SubshellNode:
    """A parenthesised subshell: `( ... )`."""

    cmd: object


@dataclass
class ChainNode:
    """Sequential/conditional chaining: `left SEP right` with SEP in ; && ||."""

    left: object
    right: object
    sep: str


ShellNode = CommandNode | PipeNode | RedirNode | SubshellNode | ChainNode


def tokenize_shell(text: str) -> list[str]:
    """Split shell text into words and operators, respecting quotes.

    Raises CommandSyntaxError on unclosed quotes or trailing backslash.
    """
    tokens: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    escaped = False
    i = 0
    n = len(text)

    def flush_word() -> None:
        if buf:
            tokens.append("".join(buf))
            buf.clear()

    while i < n:
        ch = text[i]
        if escaped:
            buf.append(ch)
            escaped = False
            i += 1
            continue
        if ch == "\\":
            if quote == "'":
                buf.append(ch)
            else:
                # Keep the backslash so later feature detection sees `\*` etc.
                # A lone trailing backslash is a syntax error (checked below).
                if i + 1 >= n:
                    raise CommandSyntaxError("trailing backslash in command")
                buf.append(ch)
                escaped = True
            i += 1
            continue
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch in (" ", "\t", "\n"):
            flush_word()
            i += 1
            continue
        # Keep `$(...)` (and `$((...))`) as one word so command
        # substitution is not split into subshell tokens.
        if ch == "$" and i + 1 < n and text[i + 1] == "(":
            buf.append("$")
            depth = 0
            j = i + 1
            inner_quote: str | None = None
            while j < n:
                c = text[j]
                if inner_quote is not None:
                    buf.append(c)
                    if c == inner_quote:
                        inner_quote = None
                    j += 1
                    continue
                if c in ("'", '"'):
                    inner_quote = c
                    buf.append(c)
                    j += 1
                    continue
                buf.append(c)
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                j += 1
            if depth != 0:
                raise CommandSyntaxError("unclosed $(...) substitution")
            i = j
            continue
        # Try longest operators first (&&, ||, >>, 2>>, ...).
        matched: str | None = None
        for op in _ALL_OPERATORS:
            if text.startswith(op, i):
                # `2>` must not split the `2` off a word like `a2>b`; only
                # treat it as redirection when it starts a fresh token.
                if op in ("2>", "2>>") and buf:
                    continue
                matched = op
                break
        if matched is not None:
            flush_word()
            tokens.append(matched)
            i += len(matched)
            continue
        buf.append(ch)
        i += 1

    if quote is not None:
        raise CommandSyntaxError("unclosed quote in command")
    flush_word()
    return tokens


def parse_command(text: str) -> ShellNode:
    """Parse shell text into a small AST.

    P1.1a scope: full tokenization + single simple command parsing.
    Compound forms (Chain/Pipe/Redir/Subshell) are completed in P1.1b;
    this entry point already exists so callers can depend on it.
    """
    tokens = tokenize_shell(text)
    if not tokens:
        return CommandNode(argv=[], env={})
    node, pos = _parse_chain(tokens, 0)
    if pos != len(tokens):
        raise CommandSyntaxError(f"unexpected token: {tokens[pos]!r}")
    return node


def _parse_chain(tokens: list[str], pos: int) -> tuple[ShellNode, int]:
    """Parse `a ; b`, `a && b`, `a || b` chains on top of pipes."""
    node, pos = _parse_pipe(tokens, pos)
    while pos < len(tokens) and tokens[pos] in CHAIN_SEPARATORS:
        sep = tokens[pos]
        if not _has_command(node):
            raise CommandSyntaxError(f"separator {sep!r} misses a command before it")
        pos += 1
        if pos >= len(tokens):
            raise CommandSyntaxError(f"separator {sep!r} misses a command after it")
        if tokens[pos] in CHAIN_SEPARATORS or tokens[pos] in ("|", ")"):
            raise CommandSyntaxError(f"separator {sep!r} misses a command after it")
        right, pos = _parse_pipe(tokens, pos)
        node = ChainNode(left=node, right=right, sep=sep)
    return node, pos


def _parse_pipe(tokens: list[str], pos: int) -> tuple[ShellNode, int]:
    """Parse `a | b | c` pipelines."""
    node, pos = _parse_factor(tokens, pos)
    while pos < len(tokens) and tokens[pos] == "|":
        if not _has_command(node):
            raise CommandSyntaxError("pipe '|' misses a command before it")
        pos += 1
        if pos >= len(tokens) or tokens[pos] in ("|", ")", *CHAIN_SEPARATORS):
            raise CommandSyntaxError("pipe '|' misses a command after it")
        right, pos = _parse_factor(tokens, pos)
        node = PipeNode(left=node, right=right)
    return node, pos


def _has_command(node: ShellNode) -> bool:
    """True when an AST branch holds at least one word or subshell."""
    for leaf in iter_command_nodes(node):
        if leaf.argv or leaf.env:
            return True
    if isinstance(node, SubshellNode):
        return True
    if isinstance(node, RedirNode):
        return True
    return False


def _parse_factor(tokens: list[str], pos: int) -> tuple[ShellNode, int]:
    """Parse one subshell or simple command with redirections."""
    if pos < len(tokens) and tokens[pos] == "(":
        pos += 1
        if pos < len(tokens) and tokens[pos] == ")":
            raise CommandSyntaxError("empty subshell '()'")
        inner, pos = _parse_chain(tokens, pos)
        if pos >= len(tokens) or tokens[pos] != ")":
            raise CommandSyntaxError("unclosed subshell: misses ')'")
        pos += 1
        node: ShellNode = SubshellNode(cmd=inner)
        # Trailing redirections on a subshell, e.g. `(ls) > out`.
        while pos < len(tokens) and tokens[pos] in REDIR_OPERATORS:
            op = tokens[pos]
            target, pos = _parse_redir_target(tokens, pos)
            node = RedirNode(cmd=node, target=target, op=op)
        return node, pos
    if pos < len(tokens) and tokens[pos] == ")":
        raise CommandSyntaxError("unexpected ')'")
    return _parse_simple_with_redir(tokens, pos)


def _parse_redir_target(tokens: list[str], pos: int) -> tuple[str, int]:
    """Validate one redirection target after tokens[pos] (the operator)."""
    op = tokens[pos]
    if pos + 1 >= len(tokens):
        raise CommandSyntaxError(f"redirection {op!r} misses a target")
    target = tokens[pos + 1]
    if target in _ALL_OPERATORS or target in CHAIN_SEPARATORS:
        raise CommandSyntaxError(f"redirection {op!r} misses a target")
    # `>&2`, `&>file` style numeric targets stay as-is; descriptors are fine.
    if not target.strip():
        raise CommandSyntaxError(f"redirection {op!r} misses a target")
    return target, pos + 2


def _split_env_assignments(words: list[str]) -> tuple[dict[str, str], list[str]]:
    env: dict[str, str] = {}
    rest = list(words)
    while rest:
        head = rest[0]
        if "=" in head and "/" not in head and not head.startswith("-"):
            key, _, value = head.partition("=")
            if key and key[0].isalpha() or key.startswith("_"):
                env[key] = value
                rest.pop(0)
                continue
        break
    return env, rest


def _parse_simple_with_redir(tokens: list[str], pos: int) -> tuple[ShellNode, int]:
    """Parse one simple command with interleaved redirections."""
    words: list[str] = []
    redirs: list[tuple[str, str]] = []
    i = pos
    while i < len(tokens):
        tok = tokens[i]
        if tok in CHAIN_SEPARATORS or tok in ("|", "(", ")"):
            break
        if tok in REDIR_OPERATORS:
            target, i = _parse_redir_target(tokens, i)
            redirs.append((tok, target))
            continue
        words.append(tok)
        i += 1
    if not words and not redirs:
        raise CommandSyntaxError("misses a command")
    env, argv = _split_env_assignments(words)
    node: ShellNode = CommandNode(argv=argv, env=env)
    for op, target in redirs:
        node = RedirNode(cmd=node, target=target, op=op)
    return node, i


def iter_command_nodes(node: ShellNode) -> list[CommandNode]:
    """Flatten an AST into its leaf simple commands (for risk scoring)."""
    if isinstance(node, CommandNode):
        return [node]
    if isinstance(node, (PipeNode, ChainNode)):
        return iter_command_nodes(node.left) + iter_command_nodes(node.right)  # type: ignore[arg-type]
    if isinstance(node, RedirNode):
        return iter_command_nodes(node.cmd)  # type: ignore[arg-type]
    if isinstance(node, SubshellNode):
        return iter_command_nodes(node.cmd)  # type: ignore[arg-type]
    return []


def iter_redir_targets(node: ShellNode) -> list[tuple[str, str]]:
    """Collect (op, target) redirections in an AST."""
    if isinstance(node, CommandNode):
        return []
    if isinstance(node, RedirNode):
        return [(node.op, node.target)] + iter_redir_targets(node.cmd)  # type: ignore[arg-type]
    if isinstance(node, (PipeNode, ChainNode)):
        return iter_redir_targets(node.left) + iter_redir_targets(node.right)  # type: ignore[arg-type]
    if isinstance(node, SubshellNode):
        return iter_redir_targets(node.cmd)  # type: ignore[arg-type]
    return []


# ---------------------------------------------------------------------------
# P1.1c: unsafe expansion + glob detection (quote-aware, for risk scoring).
# check_command() behaviour is unchanged here; P1.2 consumes these helpers.
# ---------------------------------------------------------------------------

def detect_expansions(text: str) -> list[str]:
    """List unsafe shell expansions in raw text: $(), ${}, $VAR, backticks."""
    found: list[str] = []
    quote: str | None = None
    escaped = False
    seen_var = seen_subst = seen_brace = seen_backtick = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if ch == "\\":
            # Backslash escapes next char outside single quotes.
            if quote != "'":
                escaped = True
            i += 1
            continue
        if quote is not None:
            if ch == quote:
                quote = None
            elif quote == '"' and ch == "`" and not seen_backtick:
                found.append("backtick substitution `...`")
                seen_backtick = True
            elif quote == '"' and ch == "$" and i + 1 < n:
                nxt = text[i + 1]
                if nxt == "(" and not seen_subst:
                    found.append("command substitution $(...)")
                    seen_subst = True
                elif nxt == "{" and not seen_brace:
                    found.append("parameter expansion ${...}")
                    seen_brace = True
                elif (nxt.isalpha() or nxt == "_" or nxt in "?$!#*@0123456789") and not seen_var:
                    found.append("variable expansion $VAR")
                    seen_var = True
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if ch == "`" and not seen_backtick:
            found.append("backtick substitution `...`")
            seen_backtick = True
            i += 1
            continue
        if ch == "$" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "(" and not seen_subst:
                found.append("command substitution $(...)")
                seen_subst = True
            elif nxt == "{" and not seen_brace:
                found.append("parameter expansion ${...}")
                seen_brace = True
            elif (nxt.isalpha() or nxt == "_" or nxt in "?$!#*@0123456789") and not seen_var:
                found.append("variable expansion $VAR")
                seen_var = True
        i += 1
    return found


def detect_globs(text: str) -> list[str]:
    """List unquoted glob characters (*, ?, [...]) in raw text."""
    found: list[str] = []
    quote: str | None = None
    escaped = False
    seen_star = seen_q = seen_bracket = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if ch == "\\":
            if quote != "'":
                escaped = True
            i += 1
            continue
        if quote is not None:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if ch == "*" and not seen_star:
            found.append("glob '*'")
            seen_star = True
        elif ch == "?" and not seen_q:
            found.append("glob '?'")
            seen_q = True
        elif ch == "[" and not seen_bracket:
            # Only count '[' when it looks like a character class: has a
            # closing ']' later on the same token stretch.
            j = text.find("]", i + 1)
            if j != -1 and j - i <= 64 and " " not in text[i:j]:
                found.append("glob '[...]'")
                seen_bracket = True
        i += 1
    return found


def list_shell_features(text: str) -> dict[str, list[str]]:
    """Combined expansion + glob report for one command line."""
    return {"expansions": detect_expansions(text), "globs": detect_globs(text)}


def node_shell_features(node: ShellNode) -> dict[str, list[str]]:
    """Run expansion/glob detection over every leaf command in an AST."""
    expansions: list[str] = []
    globs: list[str] = []
    for leaf in iter_command_nodes(node):
        for part in leaf.argv:
            for item in detect_expansions(part):
                if item not in expansions:
                    expansions.append(item)
            for item in detect_globs(part):
                if item not in globs:
                    globs.append(item)
    for _, target in iter_redir_targets(node):
        for item in detect_globs(target):
            if item not in globs:
                globs.append(item)
    return {"expansions": expansions, "globs": globs}

# ---------------------------------------------------------------------------
# P1.2: risk tables (weights per proposal.txt P1.2). Scoring helpers follow
# in later portions; this portion only adds constants, no behaviour change.
# ---------------------------------------------------------------------------

#: Verdict thresholds on the normalized 0-100 score.
ALLOW_MAX = 30
CONFIRM_MAX = 70

#: w1 privilege escalation (sudo/su/doas).
_PRIVILEGE_WEIGHT = 25
_PRIVILEGE_PROGS = {"sudo", "su", "doas", "runas"}

#: w2 destructiveness (rm -rf /=40, mkfs/dd=35, shutdown/reboot, fork bomb).
_DESTRUCTIVE_WEIGHT_RM_ROOT = 40
_DESTRUCTIVE_WEIGHT_FORMAT = 35
_FORMAT_PROGS = {"mkfs", "mkfs.ext4", "mkfs.vfat", "fdisk", "parted", "dd"}
_HALT_PROGS = {"shutdown", "reboot", "poweroff", "halt", "init"}

#: w3 scope (target breadth): `/`=20, `~`=10, `./`=5.
_SCOPE_WEIGHT_ROOT = 20
_SCOPE_WEIGHT_HOME = 10
_SCOPE_WEIGHT_LOCAL = 5

#: w4 irreversibility (delete/format/remove).
_IRREVERSIBLE_WEIGHT = 20
_DELETE_PROGS = {"rm", "rmdir", "shred", "wipefs"}

#: w5 network exfil / pipe-to-shell (curl|sh=15).
_NETWORK_WEIGHT = 15
_DOWNLOAD_PROGS = {"curl", "wget", "aria2c", "axel", "ftp", "nc", "ncat", "socat"}
_SHELL_PROGS = {"bash", "sh", "dash", "zsh", "fish", "ksh", "pwsh", "powershell"}

#: P1.2b allow-list: read-only / reversible commands that stay ALLOW on low score.
_ALLOW_PROGS = {
    "ls", "pwd", "whoami", "date", "uname", "df", "du", "ps", "cat", "head",
    "tail", "grep", "find", "wc", "sort", "uniq", "echo", "printf", "true",
    "false", "which", "who", "id", "uptime", "lsb_release",
}


#: System subtrees no command may create, write, or delete into (W1).
_SENSITIVE_ROOTS = ("/root", "/etc", "/boot", "/sys", "/proc", "/dev")

#: Programs whose side effects count as writes for sensitive-path denial.
_WRITING_PROGS = {"mkdir", "touch", "cp", "mv", "ln", "install", "tee", "chmod", "chown"}


def _clean_path(token: str) -> str:
    return (token or "").strip().strip("'\"")


def _under_sensitive_root(path: str) -> bool:
    cleaned = _clean_path(path)
    return any(cleaned == root or cleaned.startswith(root + "/") for root in _SENSITIVE_ROOTS)


def _has_parent_traversal(path: str) -> bool:
    return ".." in _clean_path(path).split("/")


def _check_deny_list(node: ShellNode, leaves: list[CommandNode], raw_text: str) -> str | None:
    """Hard DENY patterns that bypass score thresholds (fork bomb, raw disk)."""
    flat = raw_text.replace(" ", "")
    if ":(){" in flat:
        return "deny-list: fork bomb pattern"
    if _has_pipe_to_shell(node):
        return "deny-list: pipe-to-shell 'curl|sh' pattern"
    for _op, target in iter_redir_targets(node):
        if _under_sensitive_root(target):
            return f"deny-list: write to system path '{_clean_path(target)}'"
    for leaf in leaves:
        if not leaf.argv:
            continue
        prog, args = _effective_argv(leaf.argv)
        lowered = [a.lower() for a in args]
        paths = [_clean_path(arg) for arg in args if arg and not arg.startswith("-")]
        if any(_has_parent_traversal(path) for path in paths):
            chmod_wipe = prog == "chmod" and ("777" in lowered or "7777" in lowered)
            if prog in _DELETE_PROGS | _FORMAT_PROGS or chmod_wipe:
                return f"deny-list: traversal '../' with destructive '{prog}'"
        if any(_under_sensitive_root(path) for path in paths):
            if prog in _WRITING_PROGS or prog in _DELETE_PROGS | _FORMAT_PROGS:
                return f"deny-list: write to system path '{prog}'"
        if prog in _REMOVE_MANAGERS and any(verb in lowered for verb in REMOVE_VERBS):
            uninstall_reason = _check_uninstall_known(prog, args)
            if uninstall_reason:
                return f"deny-list: {uninstall_reason}"
        if prog in _FORMAT_PROGS:
            if prog == "dd":
                joined = " ".join(lowered)
                if "of=/dev/" not in joined and "if=/dev/" not in joined:
                    continue
            return f"deny-list: raw-disk tool '{prog}'"
        if prog in _HALT_PROGS:
            return f"deny-list: system halt via '{prog}'"
        if prog == "rm" and "--no-preserve-root" in lowered:
            return "deny-list: 'rm --no-preserve-root'"
        if prog == "rm" and _has_recursive_flag(args):
            targets = [a.strip().strip("'\"") for a in args if not a.startswith("-") or "/" in a]
            if any(t in ("/", "/*", "/**", "/.") for t in targets):
                return "deny-list: 'rm -rf /'"
        if prog == "chmod" and ("777" in lowered or "7777" in lowered):
            targets = [a for a in args if not a.startswith("-")]
            if any(t.strip().strip("'\"") in ("/", "/*", "/**") for t in targets):
                return "deny-list: 'chmod 777 /'"
        if prog == "dd":
            joined = " ".join(lowered)
            if "of=/dev/" in joined:
                return "deny-list: 'dd of=/dev/...' raw-disk write"
    return None


def _prog_name(argv0: str) -> str:
    """Basename of the executable, lowercased (`/usr/bin/sudo` -> `sudo`)."""
    return Path(argv0).name.lower() if argv0 else ""


#: Installed-state cache for uninstall checks: (manager, pkg) -> (ts, known).
_INSTALLED_CACHE: dict[tuple[str, str], tuple[float, bool | None]] = {}
_INSTALLED_CACHE_TTL = 60.0

#: Programs whose remove verbs trigger the installed-state check.
#: Short flags (-r) only count for pacman; rm/zip/ls never enter this path.
_REMOVE_MANAGERS = UNINSTALL_MANAGERS


def _installed_query_argv(prog: str, package: str) -> list[list[str]] | None:
    """Manager-native installed queries (tried in order), or None."""
    if prog == "flatpak":
        return [["flatpak", "info", package]]
    if prog in ("dnf", "zypper"):
        return [["rpm", "-q", package]]
    if prog == "apt":
        return [["dpkg-query", "-W", "-f=${Status}", package]]
    if prog == "pacman":
        return [["pacman", "-Q", package]]
    if prog == "apk":
        return [["apk", "info", "-e", package]]
    if prog == "snap":
        return [["snap", "list", package]]
    if prog == "brew":
        return [["brew", "list", package], ["brew", "list", "--cask", package]]
    return None


def is_package_installed(prog: str, package: str) -> bool | None:
    """True/False whether a manager owns a package; None when unverifiable."""
    key = (prog, package)
    now = time.monotonic()
    hit = _INSTALLED_CACHE.get(key)
    if hit is not None and now - hit[0] < _INSTALLED_CACHE_TTL:
        return hit[1]
    result: bool | None = None
    queries = _installed_query_argv(prog, package)
    if queries is not None:
        for argv in queries:
            if shutil.which(argv[0]) is None:
                continue
            try:
                completed = subprocess.run(argv, capture_output=True, text=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                continue
            if prog == "apt":
                result = "install ok installed" in (completed.stdout or "")
            else:
                result = completed.returncode == 0
            if result:
                break
    if len(_INSTALLED_CACHE) > 512:
        _INSTALLED_CACHE.clear()
    _INSTALLED_CACHE[key] = (now, result)
    return result


def _leaf_remove_package(argv: list[str]) -> str | None:
    """Package token after the remove verb in one effective argv, else None."""
    lowered = [token.lower() for token in argv]
    verb_at: int | None = None
    for index, token in enumerate(lowered):
        if token in REMOVE_VERBS:
            verb_at = index
            break
    if verb_at is None:
        return None
    skip = {
        "-y", "--yes", "-e", "--exact", "--noconfirm", "--id",
        "--silent", "-q", "--quiet", "--delete-data", "--all",
    }
    for token in argv[verb_at + 1 :]:
        if not token or token in skip or token.startswith("-"):
            continue
        if token.lower() == "flathub":
            continue
        return token
    return None


def _check_uninstall_known(prog: str, args: list[str]) -> str | None:
    """Deny uninstalls of apps that are neither installed nor known."""
    package = _leaf_remove_package([prog] + list(args))
    if not package:
        return None
    try:
        installed = is_package_installed(prog, package)
    except Exception:
        installed = None
    if installed:
        return None
    if installed is None:
        try:
            from auto_system_agent.tools import install_tool

            library = install_tool._load_app_library()
            known = any(
                package == value
                for entry in library.values()
                if isinstance(entry, dict)
                for value in entry.values()
            )
        except Exception:
            known = False
        try:
            known = known or shutil.which(package) is not None
        except Exception:
            pass
        if known:
            return None
    return (
        f"unknown application '{package}': no {prog} install record. "
        "List installed apps first (e.g. flatpak list, rpm -qa, dpkg -l)."
    )


def _score_privilege(leaves: list[CommandNode]) -> tuple[int, list[str]]:
    """w1: privilege escalation via sudo/su/doas (+25)."""
    for leaf in leaves:
        if leaf.argv and _prog_name(leaf.argv[0]) in _PRIVILEGE_PROGS:
            return _PRIVILEGE_WEIGHT, [f"privilege escalation via {leaf.argv[0]}"]
    return 0, []


def _effective_argv(argv: list[str]) -> tuple[str, list[str]]:
    """Real program + args behind privilege wrappers (`sudo -u root rm` -> rm).

    Skips leading `sudo/su/doas` flags (`-u root`, `-n`) so destructive
    inner commands cannot hide behind escalation.
    """
    if not argv:
        return "", []
    if _prog_name(argv[0]) not in _PRIVILEGE_PROGS:
        return _prog_name(argv[0]), argv[1:]
    rest = argv[1:]
    skip_next = False
    for idx, tok in enumerate(rest):
        if skip_next:
            skip_next = False
            continue
        if tok in ("-u", "--user", "-g", "--group", "-p", "--prompt"):
            skip_next = True
            continue
        if tok.startswith("-") and tok not in ("-", "--"):
            continue
        return _prog_name(tok), rest[idx + 1 :]
    return _prog_name(argv[0]), rest


def _path_scope_weight(path: str) -> tuple[int, str]:
    """w3 for one path: `/`=20, `~`=10, `./`=5, else 0."""
    p = (path or "").strip().strip("'\"")
    if not p:
        return 0, ""
    if p in ("/", "/*", "/**"):
        return _SCOPE_WEIGHT_ROOT, "scope covers filesystem root '/'"
    if p.startswith("/"):
        # Absolute path outside home still touches system breadth.
        if p in ("/etc", "/usr", "/bin", "/sbin", "/boot", "/dev"):
            return _SCOPE_WEIGHT_ROOT, f"scope covers system path '{p}'"
        return _SCOPE_WEIGHT_HOME, f"scope covers absolute path '{p}'"
    if p.startswith("~") or p.startswith("$HOME") or p.startswith("${HOME"):
        return _SCOPE_WEIGHT_HOME, f"scope covers home '{p}'"
    if p.startswith(("./", "../")) or p in (".", ".."):
        return _SCOPE_WEIGHT_LOCAL, f"scope covers local path '{p}'"
    return 0, ""


def _score_scope(leaves: list[CommandNode], redirs: list[tuple[str, str]]) -> tuple[int, list[str]]:
    """w3: widest target breadth across args and redirection targets."""
    best = 0
    reason = ""
    candidates: list[str] = []
    for leaf in leaves:
        candidates.extend(leaf.argv[1:])
    candidates.extend(target for _, target in redirs)
    for cand in candidates:
        # Skip flags; only paths/patterns carry scope.
        if cand.startswith("-") and "/" not in cand and not cand.startswith("~"):
            continue
        weight, why = _path_scope_weight(cand)
        if weight > best:
            best, reason = weight, why
    return (best, [reason] if reason else [])


def _has_recursive_flag(args: list[str]) -> bool:
    for arg in args:
        low = arg.lower()
        if low in ("-r", "-rf", "-fr", "-rm", "--recursive"):
            return True
        if low.startswith("-") and "r" in low and all(c in "rfRil" for c in low[1:]):
            # Covers `-rf`, `-fr`, `-r` bundles; keeps `-rm` out of scope creep.
            return True
    return False


def _score_destructiveness(leaves: list[CommandNode], raw_text: str) -> tuple[int, list[str]]:
    """w2: rm -rf /=40, mkfs/dd=35, halt/reboot, fork bomb, chmod 777 /."""
    if ":(){" in raw_text.replace(" ", "") or ":(){:|:&};:" in raw_text.replace(" ", ""):
        return _DESTRUCTIVE_WEIGHT_RM_ROOT, ["fork bomb ':(){:|:&};:' pattern"]
    best = 0
    reason = ""
    for leaf in leaves:
        if not leaf.argv:
            continue
        prog, args = _effective_argv(leaf.argv)
        lowered = [a.lower() for a in args]
        if prog in _FORMAT_PROGS:
            if prog == "dd":
                joined = " ".join(lowered)
                # Plain file-to-file copies score but stay out of the top band;
                # raw-disk reads/writes keep full weight via the deny path below.
                if "of=/dev/" not in joined and "if=/dev/" not in joined:
                    if 30 > best:
                        best, reason = 30, f"raw copy tool '{prog}'"
                    continue
            if _DESTRUCTIVE_WEIGHT_FORMAT > best:
                best, reason = _DESTRUCTIVE_WEIGHT_FORMAT, f"formatting/raw-disk tool '{prog}'"
        if prog in _HALT_PROGS:
            if 30 > best:
                best, reason = 30, f"system halt/reboot via '{prog}'"
        if prog == "chmod" and ("777" in lowered or "7777" in lowered):
            targets = [a for a in args if not a.startswith("-")]
            if any(t.strip().strip("'\"") in ("/", "/*", "/**") for t in targets):
                if 35 > best:
                    best, reason = 35, "permission wipe 'chmod 777 /'"
        if prog == "rm":
            targets = [a for a in args if not a.startswith("-") or "/" in a]
            hits_root = any(t.strip().strip("'\"") in ("/", "/*", "/**", "/.") for t in targets)
            if _has_recursive_flag(args) and hits_root:
                return _DESTRUCTIVE_WEIGHT_RM_ROOT, ["destructive 'rm -rf /'"]
            if _has_recursive_flag(args) and best < 30:
                best, reason = 30, "recursive delete 'rm -r'"
            elif "--no-preserve-root" in lowered:
                return _DESTRUCTIVE_WEIGHT_RM_ROOT, ["destructive 'rm --no-preserve-root'"]
    return (best, [reason] if reason else [])


def _score_irreversibility(leaves: list[CommandNode]) -> tuple[int, list[str]]:
    """w4: delete/format/remove cannot be undone (+20)."""
    for leaf in leaves:
        if leaf.argv and _effective_argv(leaf.argv)[0] in _DELETE_PROGS | _FORMAT_PROGS:
            return _IRREVERSIBLE_WEIGHT, [f"irreversible operation via '{leaf.argv[0]}'"]
    return 0, []


def _has_pipe_to_shell(node: ShellNode) -> bool:
    """True when a download/streaming prog pipes into a shell interpreter."""
    if isinstance(node, PipeNode):
        left_progs = {_effective_argv(c.argv)[0] for c in iter_command_nodes(node.left) if c.argv}  # type: ignore[arg-type]
        right_progs = {_effective_argv(c.argv)[0] for c in iter_command_nodes(node.right) if c.argv}  # type: ignore[arg-type]
        if left_progs & _DOWNLOAD_PROGS and right_progs & (_SHELL_PROGS | {"sudo", "python", "python3", "perl", "ruby", "node"}):
            return True
        # Recurse: `a | b | sh` nests on the right.
        return _has_pipe_to_shell(node.left) or _has_pipe_to_shell(node.right)  # type: ignore[arg-type]
    if isinstance(node, (ChainNode, SubshellNode, RedirNode)):
        children: list[ShellNode] = []
        if isinstance(node, ChainNode):
            children = [node.left, node.right]  # type: ignore[list-item]
        elif isinstance(node, (SubshellNode, RedirNode)):
            children = [node.cmd]  # type: ignore[list-item]
        return any(_has_pipe_to_shell(c) for c in children)
    return False


def _score_network(node: ShellNode, leaves: list[CommandNode]) -> tuple[int, list[str]]:
    """w5: network exfil / pipe-to-shell (+15)."""
    if _has_pipe_to_shell(node):
        return _NETWORK_WEIGHT, ["network pipe-to-shell 'curl|sh' pattern"]
    for leaf in leaves:
        if leaf.argv and _prog_name(leaf.argv[0]) in _DOWNLOAD_PROGS:
            joined = " ".join(leaf.argv[1:])
            if "http://" in joined or "https://" in joined or "|" in joined:
                return _NETWORK_WEIGHT, [f"network download via '{leaf.argv[0]}'"]
    return 0, []


def canonical_command(node: ShellNode) -> str:
    """Normalized single-space form via shlex.join (for audit + UI)."""
    if isinstance(node, CommandNode):
        prefix = [f"{k}={v}" for k, v in node.env.items()]
        return shlex.join(prefix + node.argv) if (prefix or node.argv) else ""
    if isinstance(node, ChainNode):
        return f"{canonical_command(node.left)} {node.sep} {canonical_command(node.right)}".strip()  # type: ignore[arg-type]
    if isinstance(node, PipeNode):
        return f"{canonical_command(node.left)} | {canonical_command(node.right)}".strip()  # type: ignore[arg-type]
    if isinstance(node, RedirNode):
        inner = canonical_command(node.cmd)  # type: ignore[arg-type]
        return f"{inner} {node.op} {node.target}".strip()
    if isinstance(node, SubshellNode):
        return f"({canonical_command(node.cmd)})"  # type: ignore[arg-type]
    return ""


def _effective_prog(leaf: CommandNode) -> str:
    """Real executable behind privilege wrappers (`sudo apt ...` -> `apt`)."""
    if not leaf.argv:
        return ""
    prog = _prog_name(leaf.argv[0])
    if prog in _PRIVILEGE_PROGS and len(leaf.argv) > 1:
        nxt = leaf.argv[1]
        if nxt.startswith("-"):
            for tok in leaf.argv[2:]:
                if not tok.startswith("-"):
                    return _prog_name(tok)
            return _prog_name(nxt)
        return _prog_name(nxt)
    return prog


def _check_path(leaves: list[CommandNode]) -> str | None:
    """PATH check via shutil.which; skips builtins and empty/redir-only leaves."""
    from auto_system_agent.platforms.os_utils import detect_os  # local import: cheap, no cycle
    _ = detect_os
    for leaf in leaves:
        if not leaf.argv:
            continue
        prog = _effective_prog(leaf)
        if not prog or prog in _SHELL_BUILTINS or prog in _OPENERS:
            continue
        # Shell syntax tokens that never live on PATH.
        if prog in (":", "[", "[[", "{", "}", "!", "time"):
            continue
        if shutil.which(prog) is None:
            return prog
    return None


def _score_expansion_glob(raw_text: str) -> tuple[int, list[str]]:
    """Dynamic-content bonus: hidden commands/globs widen blast radius."""
    points = 0
    reasons: list[str] = []
    for item in detect_expansions(raw_text):
        if "command substitution" in item or "backtick" in item:
            points = max(points, 10)
            reasons.append(f"dynamic content: {item}")
        else:
            points = max(points, 5)
            reasons.append(f"dynamic content: {item}")
        break
    for item in detect_globs(raw_text):
        points += 5
        reasons.append(f"broad match: {item}")
        break
    return min(points, 15), reasons


def score_command(text: str) -> tuple[int, list[str]]:
    """Aggregate w1..w5 + expansion/glob into a 0-100 score with reasons."""
    raw = (text or "").strip()
    if not raw:
        return 0, []
    try:
        node = parse_command(raw)
    except CommandSyntaxError as exc:
        # Unparsable input never runs silently: fork bombs stay at 100,
        # other syntax slips stay DENY-high so the caller blocks them.
        pts, why = _score_destructiveness([CommandNode(argv=[], env={})], raw)
        base = max(pts, 85)
        reasons = [f"invalid syntax: {exc}"]
        reasons.extend(why)
        return min(100, base), reasons
    leaves = iter_command_nodes(node)
    redirs = iter_redir_targets(node)
    total = 0
    reasons: list[str] = []
    for scorer in (
        _score_privilege(leaves),
        _score_destructiveness(leaves, raw),
        _score_scope(leaves, redirs),
        _score_irreversibility(leaves),
        _score_network(node, leaves),
        _score_expansion_glob(raw),
    ):
        pts, why = scorer
        total += pts
        reasons.extend(why)
    # Highest-risk leaf dominates chains: `ls; rm -rf /` must not average down.
    if len(leaves) > 1:
        worst = 0
        for leaf in leaves:
            sub = sum(
                p
                for p, _ in (
                    _score_privilege([leaf]),
                    _score_destructiveness([leaf], " ".join(leaf.argv)),
                    _score_scope([leaf], []),
                    _score_irreversibility([leaf]),
                )
            )
            worst = max(worst, sub)
        total = max(total, worst)
    return min(100, total), reasons

# Handled inside TerminalSession / the pty shell, not real executables.
_SHELL_BUILTINS = {
    "cd", "pwd", "history", "clear", "exit", "echo", "true", "false", ":",
    "test", "[", "export", "source", ".", "alias", "unalias", "type",
    "command", "hash", "printf", "pushd", "popd", "dirs", "read", "time",
}

# Linux file/URL openers: a bare word that is not a file is an app guess.
_OPENERS = {"xdg-open", "gio", "gnome-open", "kde-open", "exo-open", "wslview"}


def assess_command(command: str) -> dict:
    """Score one command line: {verdict, score, reasons, canonical_form}.

    Verdict thresholds: ALLOW<=30, CONFIRM<=70, DENY>70. Deny-list hits
    force DENY with at least 85 points; missing binaries are reported as
    reasons without changing the score (check_command still blocks them).
    """
    raw = (command or "").strip()
    if not raw:
        return {"verdict": "ALLOW", "score": 0, "reasons": [], "canonical_form": ""}
    try:
        node = parse_command(raw)
    except CommandSyntaxError:
        score, reasons = score_command(raw)
        deny = _check_deny_list(CommandNode(argv=[], env={}), [], raw)
        if deny:
            reasons = [deny] + reasons
            score = max(score, 100 if "fork bomb" in deny else 85)
        verdict = "DENY" if score > CONFIRM_MAX else ("CONFIRM" if score > ALLOW_MAX else "ALLOW")
        if deny:
            verdict = "DENY"
        return {"verdict": verdict, "score": score, "reasons": reasons, "canonical_form": raw}
    leaves = iter_command_nodes(node)
    canonical = canonical_command(node)
    score, reasons = score_command(raw)
    deny = _check_deny_list(node, leaves, raw)
    if deny:
        reasons = [deny] + reasons
        score = max(score, 85)
        return {"verdict": "DENY", "score": min(100, score), "reasons": reasons, "canonical_form": canonical}
    missing = _check_path(leaves)
    if missing:
        reasons = reasons + [f"missing binary '{missing}'"]
    if score <= ALLOW_MAX:
        verdict = "ALLOW"
    elif score <= CONFIRM_MAX:
        verdict = "CONFIRM"
    else:
        verdict = "DENY"
    return {"verdict": verdict, "score": score, "reasons": reasons, "canonical_form": canonical}


def check_command(command: str, cwd: Path | None = None) -> str | None:
    """Return an error message if the command is doomed, else None."""
    text = (command or "").strip()
    if not text:
        return None
    try:
        node = parse_command(text)
    except CommandSyntaxError as exc:
        return f"Invalid command syntax: {exc}"
    # First leaf carries the binary check, so chains like `cd /tmp && ls`
    # or `ls; rm -rf /` inspect `cd`/`ls` instead of choking on `;`/`&&`.
    leaves = [leaf for leaf in iter_command_nodes(node) if leaf.argv]
    if not leaves:
        return None
    prog = leaves[0].argv[0]
    if _prog_name(prog) in _SHELL_BUILTINS or prog in _SHELL_BUILTINS:
        return None
    if shutil.which(prog) is None:
        return _missing_binary_hint(prog)
    if len(leaves) == 1 and prog in _OPENERS and len(leaves[0].argv) > 1:
        return _opener_target_hint(prog, leaves[0].argv[1], cwd)
    return None


def _first_words(command: str) -> list[str]:
    try:
        tokens = shlex.split(command.strip(), posix=True)
    except ValueError:
        return []
    # Skip leading VAR=value assignments (e.g. `FOO=1 cmd`).
    out: list[str] = []
    for token in tokens:
        if not out and "=" in token and "/" not in token and not token.startswith("-"):
            continue
        out.append(token)
    return out


def _missing_binary_hint(prog: str) -> str:
    if prog == "open" and detect_os() == "linux":
        return (
            "command not found: open (no such file or directory in PATH). "
            "'open' is macOS-only; on Linux use 'xdg-open <file-or-URL>' for files, "
            "or launch apps via 'gtk-launch <name>.desktop' / 'flatpak run <app-id>'."
        )
    if prog == "start" and detect_os() == "linux":
        return (
            "command not found: start (no such file or directory in PATH). "
            "'start' is Windows-only; on Linux use 'xdg-open <file-or-URL>'."
        )
    return (
        f"command not found: {prog} (no such file or directory in PATH). "
        "It may not be installed on this system."
    )


def _opener_target_hint(opener: str, target: str, cwd: Path | None) -> str | None:
    lowered = target.lower()
    if "://" in target or lowered.startswith(("mailto:", "file:")):
        return None
    candidate = Path(target).expanduser()
    if candidate.is_absolute() and candidate.exists():
        return None
    if target.startswith(("~", "/", "./", "../")):
        # Explicit path that does not exist: let the shell report it.
        return None
    if cwd is not None:
        try:
            if (cwd / target).exists():
                return None
        except OSError:
            pass
    if "/" in target or target.startswith("-"):
        return None

    query = target[:-8] if target.lower().endswith(".desktop") else target
    matches = find_desktop_matches(query)
    if matches:
        lines = "\n".join(f"- {name} ({app_id})" for name, app_id in matches)
        return (
            f"No such file '{target}' for {opener}. Installed apps matching '{query}':\n"
            f"{lines}\n"
            f"Launch with: gtk-launch <name>.desktop — or run its Exec command ending with '&'."
        )
    return (
        f"No such file '{target}' for {opener}. No installed app matches '{query}'. "
        f"Check with: flatpak list --app | grep -i {query}; ls /usr/share/applications | grep -i {query}"
    )


def find_desktop_matches(query: str, dirs: list[Path] | None = None) -> list[tuple[str, str]]:
    """Search .desktop entries by app name; returns [(Name, file-id)]."""
    needle = query.strip().lower()
    if not needle:
        return []
    if dirs is None:
        home = Path.home()
        dirs = [
            home / ".local" / "share" / "applications",
            Path("/usr/share/applications"),
            Path("/var/lib/flatpak/exports/share/applications"),
            home / ".local" / "share" / "flatpak" / "exports" / "share" / "applications",
        ]
    found: list[tuple[str, str]] = []
    for directory in dirs:
        try:
            entries = sorted(directory.glob("*.desktop"))
        except OSError:
            continue
        for entry in entries:
            try:
                text = entry.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            name = _desktop_field(text, "Name")
            if needle in entry.stem.lower() or (name and needle in name.lower()):
                found.append((name or entry.stem, entry.name))
                if len(found) >= 5:
                    return found
    return found


def _desktop_field(text: str, key: str) -> str:
    for line in text.splitlines():
        if line.startswith(key + "="):
            return line.split("=", maxsplit=1)[1].strip()
    return ""
