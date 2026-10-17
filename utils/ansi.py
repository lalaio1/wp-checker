"""Terminal helpers: ANSI colours, unicode detection, centred text and boxes.

Everything here degrades gracefully: if the terminal does not support colours,
unicode or truecolor the helpers silently fall back to plain/ascii output.
"""

from __future__ import annotations

import os
import re
import shutil
import sys

__all__ = [
    "enable_ansi",
    "color_enabled",
    "unicode_enabled",
    "truecolor_enabled",
    "c",
    "strip_ansi",
    "visible_len",
    "pad",
    "center",
    "gradient",
    "terminal_width",
    "rule",
    "box",
    "info",
    "ok",
    "warn",
    "err",
    "blocked",
    "STEP",
]

_RESET = "\033[0m"

_STYLES = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "italic": "\033[3m",
    "underline": "\033[4m",
    "black": "\033[30m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "white": "\033[37m",
    "gray": "\033[90m",
    "grey": "\033[90m",
    "bright_red": "\033[91m",
    "bright_green": "\033[92m",
    "bright_yellow": "\033[93m",
    "bright_blue": "\033[94m",
    "bright_magenta": "\033[95m",
    "bright_cyan": "\033[96m",
    "bright_white": "\033[97m",
    "orange": "\033[38;5;208m",
    "purple": "\033[38;5;135m",
    "pink": "\033[38;5;212m",
}

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")

_state = {"color": None, "unicode": None, "truecolor": None}


# --------------------------------------------------------------------------- #
# capability detection
# --------------------------------------------------------------------------- #
def _enable_windows_vt() -> bool:
    """Enable VT100 processing on modern Windows consoles (cmd/powershell)."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        enable = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        for handle_id in (-11, -12):  # stdout, stderr
            handle = kernel32.GetStdHandle(handle_id)
            if handle in (0, -1):
                continue
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | enable)
        return True
    except Exception:
        return False


def enable_ansi(force: bool | None = None) -> bool:
    """Detect (once) whether ANSI escape codes should be emitted."""
    if _state["color"] is not None and force is None:
        return bool(_state["color"])

    enabled = True
    if force is not None:
        enabled = bool(force)
    else:
        if os.environ.get("NO_COLOR"):
            enabled = False
        if os.environ.get("TERM", "").lower() == "dumb":
            enabled = False
        try:
            if not sys.stdout.isatty():
                # still allow colours when explicitly forced by the user/env
                if os.environ.get("FORCE_COLOR") or os.environ.get("CLICOLOR_FORCE"):
                    enabled = True
                elif os.name != "nt":
                    enabled = False
        except Exception:
            enabled = False
        if enabled and os.name == "nt":
            enabled = _enable_windows_vt()

    _state["color"] = enabled
    return enabled


def color_enabled() -> bool:
    if _state["color"] is None:
        return enable_ansi()
    return bool(_state["color"])


def unicode_enabled() -> bool:
    """True when the active stdout encoding can render box/block glyphs."""
    if _state["unicode"] is not None:
        return bool(_state["unicode"])
    ok = False
    try:
        enc = (getattr(sys.stdout, "encoding", None) or "").lower().replace("-", "")
        if enc in {"utf8", "utf8mb4", "cp65001"}:
            ok = True
        elif not enc:
            ok = os.name != "nt"
        if ok:
            "█▏▕│┌┐└┘─✔✘".encode(sys.stdout.encoding or "utf-8")
    except Exception:
        ok = False
    _state["unicode"] = ok
    return ok


def set_unicode(value: bool) -> None:
    """Force ascii/unicode rendering (used by ``--progress plain``)."""
    _state["unicode"] = bool(value)


def truecolor_enabled() -> bool:
    if _state["truecolor"] is not None:
        return bool(_state["truecolor"])
    ok = False
    if color_enabled():
        colorterm = os.environ.get("COLORTERM", "").lower()
        term = os.environ.get("TERM", "").lower()
        ok = "truecolor" in colorterm or "24bit" in colorterm or "truecolor" in term
        if not ok and os.environ.get("TERM_PROGRAM"):
            ok = True  # iTerm / vscode / hyper / apple terminal
    _state["truecolor"] = ok
    return ok


# --------------------------------------------------------------------------- #
# colour helpers
# --------------------------------------------------------------------------- #
def c(text: str, *styles: str) -> str:
    """Wrap ``text`` in the given ANSI styles (no-op when colours are off)."""
    if not text or not styles or not color_enabled():
        return text
    prefix = "".join(_STYLES.get(s, "") for s in styles)
    if not prefix:
        return text
    return f"{prefix}{text}{_RESET}"


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def visible_len(text: str) -> int:
    return len(strip_ansi(text))


def pad(text: str, width: int) -> str:
    diff = width - visible_len(text)
    return text + (" " * diff if diff > 0 else "")


def terminal_width(default: int = 100) -> int:
    try:
        width = shutil.get_terminal_size((default, 24)).columns
    except Exception:
        width = default
    return max(40, min(width, 300))


def center(text: str, width: int | None = None) -> str:
    width = width or terminal_width()
    out = []
    for line in text.splitlines():
        padding = max(0, (width - visible_len(line)) // 2)
        out.append((" " * padding) + line)
    return "\n".join(out)


def gradient(text: str, start=(40, 140, 255), end=(0, 235, 200), mode: str = "line") -> str:
    """Blue→cyan gradient.

    ``mode="line"`` (default) paints one colour per row which keeps the escape
    sequence count tiny - important on slow terminals such as Termux.
    ``mode="char"`` interpolates per character (truecolor only).
    """
    if not color_enabled():
        return text
    lines = text.splitlines()
    if not lines:
        return text

    total = max(1, len(lines) - 1)
    if mode == "char" and truecolor_enabled():
        joined = "\n".join(lines)
        chars = len(joined) or 1
        out = []
        counter = 0
        last = None
        for char in joined:
            if char == "\n":
                counter += 1
                out.append("\n")
                continue
            ratio = counter / chars
            rgb = tuple(int(start[i] + (end[i] - start[i]) * ratio) for i in range(3))
            code = f"\033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"
            if code != last:
                out.append(code)
                last = code
            out.append(char)
            counter += 1
        out.append(_RESET)
        return "".join(out)

    out = []
    for index, line in enumerate(lines):
        if not line.strip():
            out.append(line)
            continue
        ratio = index / total if total else 0.0
        if truecolor_enabled():
            rgb = tuple(int(start[i] + (end[i] - start[i]) * ratio) for i in range(3))
            out.append(f"\033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m{line}{_RESET}")
        else:
            out.append(c(line, "bright_blue" if ratio < 0.5 else "cyan"))
    return "\n".join(out)


def rule(char: str | None = None, width: int | None = None) -> str:
    if char is None:
        char = "─" if unicode_enabled() else "-"
    return char * (width or terminal_width())


STEP = "▸" if unicode_enabled() else ">"


# --------------------------------------------------------------------------- #
# status prefixes
# --------------------------------------------------------------------------- #
def _tag(symbol: str, text: str, style: str) -> str:
    return f"{c(symbol, style)} {text}"


def info(text: str) -> str:
    return _tag("[*]" if not unicode_enabled() else "◆", text, "cyan")


def ok(text: str) -> str:
    return _tag("[+]" if not unicode_enabled() else "✔", text, "bright_green")


def warn(text: str) -> str:
    return _tag("[!]" if not unicode_enabled() else "▲", text, "bright_yellow")


def err(text: str) -> str:
    return _tag("[-]" if not unicode_enabled() else "✘", text, "bright_red")


def blocked(text: str) -> str:
    return _tag("[W]" if not unicode_enabled() else "⛔", text, "bright_magenta")


# --------------------------------------------------------------------------- #
# boxes
# --------------------------------------------------------------------------- #
def box(lines: list[str], title: str | None = None, width: int | None = None) -> str:
    """Draw a frame around ``lines`` (ANSI-aware)."""
    uni = unicode_enabled()
    inner = max([visible_len(line) for line in lines] + [visible_len(title or "")]) + 4
    if width:
        inner = max(inner, width - 2)
    if uni:
        tl, tr, bl, br, hz, vt = "┌", "┐", "└", "┘", "─", "│"
    else:
        tl, tr, bl, br, hz, vt = "+", "+", "+", "+", "-", "|"

    top = tl + (hz * inner) + tr
    if title:
        title_txt = f" {title} "
        top = tl + title_txt + (hz * (inner - visible_len(title_txt))) + tr

    out = [top]
    for line in lines:
        out.append(vt + "  " + pad(line, inner - 4) + "  " + vt)
    out.append(bl + (hz * inner) + br)
    return "\n".join(out)
