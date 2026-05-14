"""Contact sheet state (enabled + grid dims + label toggle).

Pure data — no Qt, no numpy. Held on the app singleton
(``app._contact_sheet_state``) and edited from the View menu / the
settings band; ``_on_frame_changed`` reads it to decide whether to
hijack the GL upload with a tile-grid composite.

Round-trips through :class:`Preferences` so the user's last grid
choice survives across sessions.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ContactSheetState:
    """Snapshot of the contact-sheet display.

    ``enabled`` is the master switch — when ``False`` the regular
    "topmost layer" playback path runs. The other fields are only
    consulted when ``enabled`` is ``True``.

    ``cols`` and ``rows`` are either both ``None`` (= auto, picked by
    :func:`compose.auto_grid_dimensions` to preserve the source image
    aspect ratio in the composite output) or both positive integers
    (manual grid). A partial pick — e.g. cols set, rows ``None`` —
    is normalised to auto in :meth:`effective_grid` so the UI
    doesn't have to worry about that combination.

    ``show_labels`` toggles a per-tile name overlay. Default off
    because the labels eat ~6 % of the tile height; the user opts
    in when they need the breakdown.
    """

    enabled: bool = False
    cols: int | None = None
    rows: int | None = None
    show_labels: bool = False
    # Output downscale divisor — the composite ends up at
    # ``(cols × src_w // divisor) × (rows × src_h // divisor)`` pixels.
    # ``1`` = full resolution (each tile keeps source res, big buffer);
    # ``2`` halves both dims (= one-quarter pixel count, ~4× faster
    # compose + upload, suitable for review at viewer scale).
    # The user picks the divisor to trade detail for performance.
    output_divisor: int = 1
    # Per-layer scrub offset, added on top of the global contact-sheet
    # playback offset. Lets the user pick a different "starting frame"
    # for each tile — drag horizontally on a tile to shift its offset
    # by ±N frames. Resets when contact-sheet mode is toggled off so
    # a stale offset from a previous session doesn't surprise the
    # user. Not persisted: per-tile offsets are workflow state, not
    # configuration.
    per_layer_offsets: dict[str, int] = field(default_factory=dict)

    def is_active(self) -> bool:
        """True when the GL upload should be hijacked.

        Same shape as :meth:`CompareState.is_active` — keeps the
        ``_on_frame_changed`` dispatch uniform. ``enabled`` alone is
        enough here; the layer count check happens in the decoder
        (zero layers = render nothing, fall through to the regular
        path).
        """
        return self.enabled

    def effective_grid(
        self,
        n_layers: int,
        image_aspect: float,
        canvas_aspect: float | None = None,
    ) -> tuple[int, int]:
        """Resolve the active ``(cols, rows)`` pair.

        Three cases, in order of precedence:

        * **Both ``cols`` and ``rows`` set** — manual grid; returned
          verbatim. With ``n_layers > cols * rows`` the trailing
          layers don't get a tile; this is the "I want exactly this
          layout" mode the user opts into.
        * **One dimension set, the other ``None``** — partial pick.
          The set dimension is honoured and the other is computed
          to fit every layer: ``cols = ceil(n / rows)`` (when rows
          is fixed) or symmetrically for cols. This is the menu
          preset's "N rows" / "N columns" mode — the user picks the
          axis that matters and we expand the other to accommodate
          the whole stack.
        * **Both ``None``** — auto. Routes to
          :func:`auto_grid_dimensions`:
          * with ``canvas_aspect`` — :func:`smart_grid_dimensions`
            picks the grid maximising per-tile area inside a canvas
            of that aspect;
          * without it — classic ``ceil(sqrt(n))`` square grid.

        Clamps to at least ``(1, 1)`` so the caller never has to
        guard against zero.
        """
        from img_player.contact_sheet.compose import auto_grid_dimensions  # noqa: PLC0415 — avoid cycle
        n = max(1, n_layers)
        cols_set = self.cols is not None and self.cols > 0
        rows_set = self.rows is not None and self.rows > 0
        if cols_set and rows_set:
            return (self.cols, self.rows)  # type: ignore[return-value]
        if rows_set:
            # rows fixed → cols = ceil(n / rows). Integer-only math:
            # ``(n + rows - 1) // rows`` avoids a float division.
            rows = self.rows  # type: ignore[assignment]
            cols = max(1, (n + rows - 1) // rows)
            return (cols, rows)
        if cols_set:
            cols = self.cols  # type: ignore[assignment]
            rows = max(1, (n + cols - 1) // cols)
            return (cols, rows)
        return auto_grid_dimensions(n, image_aspect, canvas_aspect)

    def to_dict(self) -> dict[str, object]:
        """JSON-friendly dump for prefs persistence."""
        return {
            "enabled": self.enabled,
            "cols": self.cols,
            "rows": self.rows,
            "show_labels": self.show_labels,
            "output_divisor": self.output_divisor,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ContactSheetState":
        """Reverse of :meth:`to_dict`. Unknown / malformed values
        fall back to defaults so a corrupt pref never crashes the
        load."""
        def _opt_pos_int(v: object) -> int | None:
            if v is None:
                return None
            # QSettings round-trips ``None`` as the literal string
            # ``"None"`` on POSIX .conf files (and on Windows when
            # the value was stored via :meth:`Preferences.contact_sheet_state`
            # which normalises ``None`` → ``"None"`` to disambiguate
            # from ``""`` / 0).
            if isinstance(v, str) and v.strip().lower() in ("none", ""):
                return None
            try:
                iv = int(v)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None
            return iv if iv > 0 else None

        def _pos_int(v: object, default: int) -> int:
            try:
                iv = int(v)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return default
            return iv if iv > 0 else default

        return cls(
            enabled=bool(data.get("enabled", False)),
            cols=_opt_pos_int(data.get("cols")),
            rows=_opt_pos_int(data.get("rows")),
            show_labels=bool(data.get("show_labels", False)),
            output_divisor=_pos_int(data.get("output_divisor"), 1),
        )
