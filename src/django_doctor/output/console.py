"""Console abstraction used by every command.

Wraps :mod:`rich` and centralises verbosity (quiet / normal / verbose), colour,
symbols and confirmation prompts, so commands never talk to ``print`` directly.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable
from enum import IntEnum
from typing import Any

from rich.console import Console as RichConsole
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.theme import Theme

from django_doctor.branding import PRODUCT_NAME
from django_doctor.security import strip_control


def _clean(objects: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(strip_control(o) if isinstance(o, str) else o for o in objects)

THEME = Theme(
    {
        "ok": "green",
        "warn": "yellow",
        "err": "bold red",
        "info": "cyan",
        "muted": "dim",
        "heading": "bold",
        "cmd": "bold cyan",
        "risk.none": "green",
        "risk.low": "green",
        "risk.medium": "yellow",
        "risk.high": "bold red",
        "risk.unknown": "magenta",
    }
)


class Verbosity(IntEnum):
    QUIET = 0
    NORMAL = 1
    VERBOSE = 2


SYMBOLS = {"ok": "✓", "warn": "⚠", "err": "✗", "info": "•", "skip": "–", "arrow": "→"}
ASCII_SYMBOLS = {"ok": "OK", "warn": "!!", "err": "XX", "info": "*", "skip": "-", "arrow": "->"}


def _supports_unicode(stream: Any) -> bool:
    encoding = (getattr(stream, "encoding", None) or "").lower()
    return "utf" in encoding


class Console:
    def __init__(
        self,
        *,
        verbosity: Verbosity = Verbosity.NORMAL,
        color: bool = True,
        debug: bool = False,
        assume_yes: bool = False,
        stdout: Any = None,
        stderr: Any = None,
        interactive: bool | None = None,
    ) -> None:
        no_color = not color or bool(os.environ.get("NO_COLOR"))
        out = stdout or sys.stdout
        err = stderr or sys.stderr
        self.verbosity = verbosity
        self.debug_enabled = debug
        self.assume_yes = assume_yes
        self._out = RichConsole(
            file=out, theme=THEME, no_color=no_color, highlight=False, soft_wrap=True,
            force_terminal=None if not no_color else False,
        )
        self._err = RichConsole(
            file=err, theme=THEME, no_color=no_color, highlight=False, soft_wrap=True,
            force_terminal=None if not no_color else False,
        )
        self.symbols = SYMBOLS if _supports_unicode(out) else ASCII_SYMBOLS
        if interactive is None:
            interactive = bool(getattr(sys.stdin, "isatty", lambda: False)()) and bool(
                getattr(out, "isatty", lambda: False)()
            )
        self.interactive = interactive

    # ------------------------------------------------------------------ basics
    @property
    def quiet(self) -> bool:
        return self.verbosity <= Verbosity.QUIET

    @property
    def verbose(self) -> bool:
        return self.verbosity >= Verbosity.VERBOSE

    def print(self, *objects: Any, **kwargs: Any) -> None:
        """Always printed, even in quiet mode (results the user asked for)."""
        self._out.print(*_clean(objects), **kwargs)

    def out(self, *objects: Any, **kwargs: Any) -> None:
        """Normal-verbosity output."""
        if not self.quiet:
            self._out.print(*_clean(objects), **kwargs)

    def raw(self, text: str) -> None:
        """Write text without markup interpretation (always printed)."""
        self._out.print(strip_control(text), markup=False, highlight=False)

    def blank(self) -> None:
        self.out("")

    # ------------------------------------------------------------ structure
    def title(self, text: str) -> None:
        self.out(f"[heading]{escape(PRODUCT_NAME)} — {escape(text)}[/heading]" if text else f"[heading]{escape(PRODUCT_NAME)}[/heading]")
        self.blank()

    def section(self, text: str) -> None:
        self.out("")
        self.out(f"[heading]{escape(text)}[/heading]")

    def rule(self, text: str = "") -> None:
        self.out(Rule(escape(text), style="muted"))

    def panel(self, body: Any, title: str | None = None, style: str = "info") -> None:
        self.print(Panel(body, title=title, border_style=style, expand=False))

    # ------------------------------------------------------------ statuses
    def success(self, text: str, indent: int = 0) -> None:
        self.out(f"{' ' * indent}[ok]{self.symbols['ok']}[/ok] {escape(text)}")

    def warning(self, text: str, indent: int = 0) -> None:
        # Warnings are shown in quiet mode too: they are about safety.
        self.print(f"{' ' * indent}[warn]{self.symbols['warn']} {escape(text)}[/warn]")

    def error(self, text: str, indent: int = 0) -> None:
        self.print(f"{' ' * indent}[err]{self.symbols['err']} {escape(text)}[/err]")

    def info(self, text: str, indent: int = 0) -> None:
        self.out(f"{' ' * indent}[info]{self.symbols['info']}[/info] {escape(text)}")

    def skipped(self, text: str, indent: int = 0) -> None:
        self.out(f"{' ' * indent}[muted]{self.symbols['skip']} {escape(text)}[/muted]")

    def detail(self, text: str, indent: int = 2, style: str | None = None) -> None:
        body = escape(text)
        self.out(f"{' ' * indent}[{style}]{body}[/{style}]" if style else f"{' ' * indent}{body}")

    def lines(self, lines: Iterable[str], indent: int = 2, style: str | None = None) -> None:
        for line in lines:
            self.detail(line, indent=indent, style=style)

    def command(self, cmd: str, indent: int = 4) -> None:
        self.print(f"{' ' * indent}[cmd]{escape(cmd)}[/cmd]")

    def debug(self, text: str) -> None:
        if self.verbose or self.debug_enabled:
            self._err.print(f"[muted]debug: {escape(text)}[/muted]")

    def stderr(self, *objects: Any, **kwargs: Any) -> None:
        self._err.print(*_clean(objects), **kwargs)

    # --------------------------------------------------------- confirmation
    def confirm(self, question: str, *, default: bool = False) -> bool:
        """Ordinary (non-destructive) confirmation. ``--yes`` answers it."""
        if self.assume_yes:
            return True
        if not self.interactive:
            return False
        suffix = " [Y/n] " if default else " [y/N] "
        try:
            answer = input(question + suffix).strip().lower()
        except EOFError:
            return False
        if not answer:
            return default
        return answer in {"y", "yes"}

    def confirm_destructive(self, question: str, *, keyword: str = "apply") -> bool:
        """Confirmation for operations that can lose data.

        ``--yes`` deliberately does *not* answer this. It only works on an
        interactive terminal and requires typing ``keyword`` in full.
        """
        if not self.interactive:
            return False
        try:
            answer = input(f"{question}\nType '{keyword}' to continue, anything else to abort: ")
        except EOFError:
            return False
        return answer.strip() == keyword
