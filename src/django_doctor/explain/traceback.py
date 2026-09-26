"""Parse Python/Django error output into structured data.

Handles:

* standard tracebacks, including chained exceptions (``__cause__`` /
  ``__context__``) and Python 3.11+ caret lines;
* ``SyntaxError`` frames (no ``in <function>`` part);
* multi-line messages (PostgreSQL ``DETAIL``/``HINT``, ``SystemCheckError``);
* errors printed without a traceback (``CommandError: ...``);
* noise: ANSI colours, pytest ``E   `` prefixes, docker-compose ``web_1 |``
  prefixes, runserver access-log lines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

TRACEBACK_START = "Traceback (most recent call last):"
CHAIN_MARKERS = (
    "The above exception was the direct cause of the following exception:",
    "During handling of the above exception, another exception occurred:",
)

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_FRAME = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>.+))?\s*$')
_EXC_LINE = re.compile(r"^(?P<type>(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*)(?::(?:\s(?P<msg>.*))?)?$")
_LOG_LINE = re.compile(
    r"^(\[\d|\d{4}-\d{2}-\d{2}[ T]\d|Watching for file changes|Performing system checks|"
    r"System check identified no issues|Starting development server|Quit the server|"
    r"Django version \d)"
)
_REQUEST = re.compile(r"(?:Internal Server Error|Bad Request|Forbidden \([^)]*\)|Not Found): (?P<path>/\S*)")
_CARETS = re.compile(r"^\s*[\^~]+\s*$")

#: Exception names that do not follow the *Error / *Exception naming rule.
KNOWN_EXCEPTIONS = frozenset(
    {
        "NoReverseMatch", "TemplateDoesNotExist", "ImproperlyConfigured", "DoesNotExist",
        "MultipleObjectsReturned", "FieldDoesNotExist", "AppRegistryNotReady", "DisallowedHost",
        "DisallowedRedirect", "SuspiciousOperation", "PermissionDenied", "Http404",
        "InconsistentMigrationHistory", "IrreversibleError", "NodeNotFoundError",
        "KeyboardInterrupt", "SystemExit", "StopIteration", "Resolver404", "ObjectDoesNotExist",
        "RequestDataTooBig", "TooManyFieldsSent", "EmptyResultSet", "FullResultSet",
        "UndefinedValueError", "SynchronousOnlyOperation", "BadSignature", "SignatureExpired",
    }
)


def _is_exception_name(name: str) -> bool:
    last = name.rsplit(".", 1)[-1]
    if not last or not last[0].isupper():
        return False
    return last.endswith(("Error", "Exception", "Warning", "Exit", "Interrupt")) or last in KNOWN_EXCEPTIONS


def looks_like_exception_line(line: str) -> bool:
    match = _EXC_LINE.match(line.strip())
    return bool(match) and _is_exception_name(match.group("type")) and (":" in line or "." in match.group("type") or match.group("type") in KNOWN_EXCEPTIONS)


@dataclass
class Frame:
    file: str
    line: int
    function: str | None = None
    code: str | None = None

    @property
    def is_library(self) -> bool:
        path = self.file.replace("\\", "/")
        return (
            "site-packages/" in path
            or "dist-packages/" in path
            or path.startswith("<")
            or "/lib/python" in path
            or "/django/" in path and "/site-packages/" in path
        )

    def is_within(self, root: Path) -> bool:
        try:
            Path(self.file).resolve().relative_to(root.resolve())
            return not self.is_library
        except (ValueError, OSError):
            return False

    def short(self, root: Path | None = None) -> str:
        path = self.file
        if root is not None:
            try:
                path = str(Path(self.file).resolve().relative_to(root.resolve()))
            except (ValueError, OSError):
                pass
        return f"{path}:{self.line}" + (f" in {self.function}" if self.function else "")


@dataclass
class ExceptionInfo:
    type: str
    message: str
    frames: list[Frame] = field(default_factory=list)

    @property
    def short_type(self) -> str:
        return self.type.rsplit(".", 1)[-1]

    @property
    def module(self) -> str:
        return self.type.rsplit(".", 1)[0] if "." in self.type else ""

    @property
    def first_line(self) -> str:
        return self.message.splitlines()[0] if self.message else ""

    def is_a(self, *names: str) -> bool:
        return self.short_type in names or self.type in names


@dataclass
class ParsedError:
    exceptions: list[ExceptionInfo]
    raw: str
    request_path: str | None = None
    had_traceback: bool = True

    @property
    def final(self) -> ExceptionInfo:
        return self.exceptions[-1]

    @property
    def frames(self) -> list[Frame]:
        return [f for exc in self.exceptions for f in exc.frames]

    def find(self, *names: str) -> ExceptionInfo | None:
        """Most recent exception in the chain matching any of ``names``."""
        for exc in reversed(self.exceptions):
            if exc.is_a(*names):
                return exc
        return None

    def project_frame(self, root: Path | None = None) -> Frame | None:
        """The innermost frame in the user's own code, if identifiable."""
        for exc in reversed(self.exceptions):
            for frame in reversed(exc.frames):
                if root is not None:
                    if frame.is_within(root):
                        return frame
                elif not frame.is_library:
                    return frame
        return None


# ------------------------------------------------------------------ cleaning
def clean_output(text: str) -> list[str]:
    text = _ANSI.sub("", text.replace("\r\n", "\n"))
    lines = text.split("\n")
    lines = _strip_common_prefix(lines)
    out = []
    for line in lines:
        # pytest "E   " prefixed failure lines
        if re.match(r"^E {1,4}", line):
            line = line[1:].lstrip(" ") if not line[1:].startswith("    ") else line[4:]
        out.append(line.rstrip())
    return out


def _strip_common_prefix(lines: list[str]) -> list[str]:
    """Remove container log prefixes such as ``web_1  | `` when present."""
    prefix = None
    for line in lines:
        idx = line.find(TRACEBACK_START)
        if idx > 0:
            candidate = line[:idx]
            if re.fullmatch(r"[\w.\-]+\s*\|\s?", candidate):
                prefix = candidate
                break
    if not prefix:
        return lines
    return [line[len(prefix):] if line.startswith(prefix) else line for line in lines]


# ------------------------------------------------------------------- parsing
def parse_error(text: str) -> ParsedError | None:
    """Parse the *latest* error found in ``text``. Returns None if none found."""
    lines = clean_output(text)
    starts = [i for i, line in enumerate(lines) if line.strip() == TRACEBACK_START]
    if starts:
        # Walk back over chained tracebacks belonging to the last error.
        group = [starts[-1]]
        for idx in reversed(starts[:-1]):
            between = [line.strip() for line in lines[idx:group[0]] if line.strip()]
            if between and between[-1] in CHAIN_MARKERS:
                group.insert(0, idx)
            else:
                break
        exceptions = []
        for n, start in enumerate(group):
            end = group[n + 1] if n + 1 < len(group) else len(lines)
            exc = _parse_segment(lines[start + 1:end])
            if exc:
                exceptions.append(exc)
        if exceptions:
            request_path = _find_request(lines[: group[0]])
            return ParsedError(exceptions, text, request_path=request_path, had_traceback=True)

    # No traceback: look for the last "SomeError: message" style line.
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i].strip()
        if looks_like_exception_line(line) and not lines[i].startswith((" ", "\t")):
            match = _EXC_LINE.match(line)
            assert match
            message_lines = [match.group("msg") or ""]
            for extra in lines[i + 1:i + 60]:
                if _LOG_LINE.match(extra):
                    break
                message_lines.append(extra)
            message = "\n".join(message_lines).strip()
            exc = ExceptionInfo(match.group("type"), message)
            return ParsedError([exc], text, request_path=_find_request(lines[:i]), had_traceback=False)

    # Well-known Django warnings printed without an exception type.
    for line in reversed(lines):
        if re.search(r"You have \d+ unapplied migration\(s\)|have changes that are not yet reflected in a migration|"
                     r"System check identified some issues|It is impossible to (?:add|change) ", line):
            idx = lines.index(line)
            message = "\n".join(lines[idx:idx + 30]).strip()
            return ParsedError([ExceptionInfo("DjangoWarning", message)], text, had_traceback=False)

    # Django's CSRF / request log lines carry useful errors without a type.
    for line in reversed(lines):
        m = re.search(r"Forbidden \((?P<reason>[^)]*)\): (?P<path>/\S*)", line)
        if m:
            exc = ExceptionInfo("CsrfViewMiddleware.Forbidden", m.group("reason"))
            return ParsedError([exc], text, request_path=m.group("path"), had_traceback=False)
    return None


def _find_request(lines: list[str]) -> str | None:
    for line in reversed(lines[-20:]):
        m = _REQUEST.search(line)
        if m:
            return m.group("path")
    return None


def _parse_segment(lines: list[str]) -> ExceptionInfo | None:
    frames: list[Frame] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        frame_match = _FRAME.match(line)
        if frame_match:
            frame = Frame(
                file=frame_match.group("file"),
                line=int(frame_match.group("line")),
                function=(frame_match.group("func") or None),
            )
            # Code line(s) follow, more indented; skip caret markers.
            j = i + 1
            while j < n and lines[j].startswith("    ") and not _FRAME.match(lines[j]):
                if not _CARETS.match(lines[j]) and frame.code is None:
                    frame.code = lines[j].strip()
                j += 1
            frames.append(frame)
            i = j
            continue
        stripped = line.strip()
        if not stripped or line.startswith((" ", "\t")):
            i += 1
            continue
        if stripped in CHAIN_MARKERS:
            break
        match = _EXC_LINE.match(stripped)
        if match and (_is_exception_name(match.group("type")) or ":" in stripped):
            message_lines = [match.group("msg") or ""]
            for extra in lines[i + 1:i + 80]:
                if extra.strip() in CHAIN_MARKERS or extra.strip() == TRACEBACK_START or _LOG_LINE.match(extra):
                    break
                message_lines.append(extra)
            message = "\n".join(message_lines).strip()
            return ExceptionInfo(match.group("type"), message, frames)
        i += 1
    if frames:
        return ExceptionInfo("UnknownError", "", frames)
    return None


def from_probe_error(error: dict) -> ParsedError:
    """Build a ParsedError from the structured error the probe returns."""
    tb = error.get("traceback") or ""
    parsed = parse_error(tb) if tb else None
    if parsed is not None:
        return parsed
    qualified = f"{error.get('module')}.{error.get('type')}" if error.get("module") not in (None, "builtins") else error.get("type", "Error")
    return ParsedError([ExceptionInfo(qualified, error.get("message", ""))], tb, had_traceback=bool(tb))
