"""
Terminal progress reporter — spinner + step + substep + elapsed time.
No external dependencies; works on any ANSI terminal.

Singleton usage from deep modules:
    from src.cli.progress import substep
    substep("OCR page 3/50")
"""

from __future__ import annotations

import sys
import threading
import time

_SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"
_DIM    = "\033[2m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"


def _fmt(s: float) -> str:
    if s < 60:
        return f"{s:.1f}s"
    return f"{int(s // 60)}m {s % 60:.0f}s"


def _clear_lines(n: int) -> None:
    """Move cursor up n lines and clear each one."""
    for _ in range(n):
        sys.stdout.write("\033[1A\033[2K")


class Progress:
    """
    Single active step + optional substep line rendered below the spinner.

    Timeline:
        p.step("Probing portals")
        p.substep("keyword 1/44")        # updates second line live
        p.done("Probe — 2 active")
    """

    def __init__(self) -> None:
        self._lock       = threading.Lock()
        self._label      = ""
        self._substep    = ""
        self._step_t     = time.monotonic()
        self._run_t      = time.monotonic()
        self._thread: threading.Thread | None = None
        self._running    = False
        self._tty        = sys.stdout.isatty()
        self._drawn      = 0   # lines currently on screen from spinner

    # ── public API ─────────────────────────────────────────────────────────────

    def step(self, label: str) -> None:
        self._stop_spinner()
        with self._lock:
            self._label   = label
            self._substep = ""
            self._step_t  = time.monotonic()
        self._start_spinner()

    def substep(self, text: str) -> None:
        with self._lock:
            self._substep = text

    def done(self, label: str | None = None) -> None:
        elapsed = time.monotonic() - self._step_t
        self._stop_spinner()
        self._println(
            f"{_GREEN}✓{_RESET}  {label or self._label}"
            f"  {_DIM}{_fmt(elapsed)}{_RESET}"
        )

    def warn(self, label: str) -> None:
        elapsed = time.monotonic() - self._step_t
        self._stop_spinner()
        self._println(
            f"{_YELLOW}⚠{_RESET}  {label}"
            f"  {_DIM}{_fmt(elapsed)}{_RESET}"
        )

    def fail(self, label: str) -> None:
        elapsed = time.monotonic() - self._step_t
        self._stop_spinner()
        self._println(
            f"{_RED}✗{_RESET}  {label}"
            f"  {_DIM}{_fmt(elapsed)}{_RESET}"
        )

    def info(self, label: str) -> None:
        """Print a static info line without touching the spinner."""
        was_running = self._running
        if was_running:
            self._stop_spinner()
        self._println(f"  {_DIM}│{_RESET}  {label}")
        if was_running and self._label:
            self._start_spinner()

    def summary(self, records: int, cost_usd: float) -> None:
        self._stop_spinner()
        total = time.monotonic() - self._run_t
        bar = "─" * 52
        self._println(f"\n{bar}")
        self._println(
            f"  {_GREEN}{_BOLD}Done{_RESET}"
            f"  {_BOLD}{records}{_RESET} records"
            f"   {_DIM}${cost_usd:.4f}{_RESET}"
            f"   {_DIM}{_fmt(total)} total{_RESET}"
        )
        self._println(bar)

    # ── internals ──────────────────────────────────────────────────────────────

    def _start_spinner(self) -> None:
        if not self._tty:
            sys.stdout.write(f"  → {self._label}\n")
            sys.stdout.flush()
            return
        self._running = True
        self._drawn   = 0
        self._thread  = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _stop_spinner(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=0.5)
            self._thread = None
        if self._tty and self._drawn > 0:
            _clear_lines(self._drawn)
            self._drawn = 0

    def _spin(self) -> None:
        i = 0
        while self._running:
            with self._lock:
                label   = self._label
                sub     = self._substep
            elapsed = time.monotonic() - self._step_t
            total   = time.monotonic() - self._run_t
            frame   = _SPINNER[i % len(_SPINNER)]

            # Clear previously drawn lines
            if self._drawn > 0:
                _clear_lines(self._drawn)

            # Line 1 — spinner + label + time
            line1 = (
                f"{_CYAN}{frame}{_RESET}  {label}"
                f"  {_DIM}{_fmt(elapsed)} / {_fmt(total)} total{_RESET}"
            )
            sys.stdout.write(line1 + "\n")

            # Line 2 — substep (if any)
            if sub:
                sys.stdout.write(f"   {_DIM}└─ {sub}{_RESET}\n")
                self._drawn = 2
            else:
                self._drawn = 1

            sys.stdout.flush()
            time.sleep(0.1)
            i += 1

    def _println(self, text: str) -> None:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()


# ── Global singleton ───────────────────────────────────────────────────────────

_instance: Progress | None = None


def set_progress(p: Progress) -> None:
    global _instance
    _instance = p


def substep(text: str) -> None:
    """Call from any module to update the current substep line."""
    if _instance is not None:
        _instance.substep(text)
