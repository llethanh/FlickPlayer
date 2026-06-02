"""Token substitution for burnin text elements.

Templates carry text like ``"frame {frame}/{frame_total}"``; the
renderer asks :func:`resolve` to expand the ``{tokens}`` against a
per-render :class:`RenderContext` snapshot. The split keeps the
renderer free of any cache / controller / preferences plumbing —
it only sees a plain dataclass of values.

Supported tokens
----------------

==============   ================================================
``{frame}``      Master frame number under the playhead.
``{frame_total}`` Last frame of the navigable range.
``{layer_frame}`` Source frame on the layer covering the playhead,
                 i.e. the on-disk frame number (``shot.0220.png``
                 → ``220``). Empty when no layer covers the
                 master frame.
``{layer_frame_total}`` Layer's own ``layer_out`` — the upper bound
                 of its source-frame range. Pairs with
                 ``{layer_frame}`` for an "N/total" readout.
``{timecode}``   ``HH:MM:SS:FF`` at the playhead, when fps is set.
``{fps}``        Playback fps, ``"24"`` / ``"25"`` / ``"23.976"``.
``{resolution}`` ``"1920x1080"`` (or ``""`` if not yet probed).
``{sequence}``   Display pattern of the loaded sequence, e.g.
                 ``"SH0010_Rendered_RGB.####.exr"``.
``{layer_name}`` Topmost-visible layer's display name.
``{date}``       Local date, ``YYYY-MM-DD``.
``{user}``       Current OS user (``%USERNAME%`` on Windows).
``{session_name}`` Loaded ``.session`` file's basename, ``""``
                 when no session is active.
==============   ================================================

Behaviour rules
---------------

* Unknown tokens stay literal (``"hello {whatever}"`` → ``"hello {whatever}"``).
  This is on purpose: the editor's live preview shows exactly what
  the template author typed, so a typo is obvious.
* Empty values render as ``""`` — the surrounding text stays.
  ``"frame {frame}/{frame_total}"`` with ``frame_total = ""`` reads
  ``"frame 1042/"`` which is the cue the data is missing.
* Doubled braces ``{{`` / ``}}`` are literal-brace escapes.
* Substitution is single-pass — a token that expands to a string
  containing another ``{token}`` is NOT recursed (avoids accidental
  recursion attacks on a hand-edited template).
"""

from __future__ import annotations

import datetime
import os
import re
from dataclasses import dataclass

# Pattern: balanced single braces, NOT preceded or followed by another
# brace (so ``{{`` / ``}}`` get left alone for the unescape pass).
# Inside the braces: identifier-like name (letters, digits, underscores).
_TOKEN_RE = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")


@dataclass(frozen=True)
class RenderContext:
    """Snapshot of the values a burnin template can substitute.

    Built by the caller (``app.py`` for the live path, the export
    engine for the bake path) from the controller + layer-stack +
    preferences. Frozen so the renderer can read it without locking.

    Every field has a sensible empty default — the template draws
    whatever it has and leaves the rest blank. The renderer never
    crashes on a missing field.
    """

    frame: int | None = None
    frame_total: int | None = None
    # ``layer_frame`` / ``layer_frame_total`` are the source-frame
    # numbering the user sees on disk for the topmost-visible
    # layer at the playhead. Independent of the master timeline so
    # a burnin can show both ("frame 1042/1244 · layer 220/350").
    # ``None`` collapses to ``""`` in :func:`resolve` — the
    # surrounding text is the cue that the value is missing.
    layer_frame: int | None = None
    layer_frame_total: int | None = None
    fps: float | None = None
    width: int | None = None
    height: int | None = None
    sequence: str = ""
    layer_name: str = ""
    session_name: str = ""
    # When None at construction time, ``resolve`` substitutes the
    # current local date / user. Explicit values let tests run
    # deterministically and the export path stamp a fixed date if
    # the user wants reproducible burnins across re-renders.
    date: str | None = None
    user: str | None = None


# ---------------------------------------------------------------------- Resolvers

def _format_timecode(frame: int | None, fps: float | None) -> str:
    """``HH:MM:SS:FF`` rendering. Returns ``""`` when either input is
    missing — the template's surrounding text shows the gap."""
    if frame is None or fps is None or fps <= 0:
        return ""
    total_frames = max(0, int(frame))
    fps_int = max(1, int(round(fps)))
    seconds_total, ff = divmod(total_frames, fps_int)
    hh, rem = divmod(seconds_total, 3600)
    mm, ss = divmod(rem, 60)
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def _format_fps(fps: float | None) -> str:
    """Three significant digits for non-integer rates (``"23.976"``),
    integer for round values (``"24"``)."""
    if fps is None or fps <= 0:
        return ""
    if abs(fps - round(fps)) < 1e-3:
        return f"{int(round(fps))}"
    return f"{fps:.3f}".rstrip("0").rstrip(".")


def _format_resolution(width: int | None, height: int | None) -> str:
    if not width or not height or width <= 0 or height <= 0:
        return ""
    return f"{int(width)}x{int(height)}"


def _current_date() -> str:
    return datetime.date.today().isoformat()


def _current_user() -> str:
    """Best-effort OS username. ``%USERNAME%`` on Windows, ``$USER``
    elsewhere. Falls back to ``""`` if neither is set."""
    return os.environ.get("USERNAME") or os.environ.get("USER") or ""


def _values_for(ctx: RenderContext) -> dict[str, str]:
    """Materialise every supported token from ``ctx``. The dict drives
    a single ``str.replace`` walk in :func:`resolve`."""
    frame_str = "" if ctx.frame is None else str(int(ctx.frame))
    total_str = (
        "" if ctx.frame_total is None else str(int(ctx.frame_total))
    )
    layer_frame_str = (
        "" if ctx.layer_frame is None else str(int(ctx.layer_frame))
    )
    layer_total_str = (
        "" if ctx.layer_frame_total is None
        else str(int(ctx.layer_frame_total))
    )
    return {
        "frame": frame_str,
        "frame_total": total_str,
        "layer_frame": layer_frame_str,
        "layer_frame_total": layer_total_str,
        "timecode": _format_timecode(ctx.frame, ctx.fps),
        "fps": _format_fps(ctx.fps),
        "resolution": _format_resolution(ctx.width, ctx.height),
        "sequence": ctx.sequence,
        "layer_name": ctx.layer_name,
        "session_name": ctx.session_name,
        "date": ctx.date if ctx.date is not None else _current_date(),
        "user": ctx.user if ctx.user is not None else _current_user(),
    }


# ---------------------------------------------------------------------- Public API

# ``{{`` and ``}}`` mark literal braces (escape sequence borrowed
# from :py:meth:`str.format`). We swap them to a non-printing
# sentinel before substitution and swap back after, so the
# regex below doesn't have to handle them.
_OPEN_SENTINEL = "\x00OPEN\x00"
_CLOSE_SENTINEL = "\x00CLOSE\x00"


def resolve(text: str, ctx: RenderContext) -> str:
    """Replace ``{token}`` placeholders in ``text`` with values from
    ``ctx``. Unknown tokens stay literal so a typo shows up in the
    editor preview rather than silently disappearing. Doubled braces
    (``{{`` / ``}}``) are escape sequences for literal braces."""
    if not text:
        return ""
    values = _values_for(ctx)

    # Step 1 — unescape ``{{`` / ``}}`` to sentinels so the regex
    # doesn't see them as legitimate token braces.
    masked = text.replace("{{", _OPEN_SENTINEL).replace(
        "}}", _CLOSE_SENTINEL,
    )

    # Step 2 — substitute every recognised token; leave unknown
    # tokens (``{whatever}`` not in ``values``) verbatim.
    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in values:
            return values[name]
        return match.group(0)
    expanded = _TOKEN_RE.sub(_replace, masked)

    # Step 3 — restore the escaped braces.
    return (
        expanded
        .replace(_OPEN_SENTINEL, "{")
        .replace(_CLOSE_SENTINEL, "}")
    )


def supported_tokens() -> tuple[str, ...]:
    """Tuple of every token name :func:`resolve` recognises — used by
    the editor's token-autocomplete dropdown."""
    return (
        "frame", "frame_total",
        "layer_frame", "layer_frame_total",
        "timecode", "fps", "resolution",
        "sequence", "layer_name", "session_name", "date", "user",
    )
