"""Channel-selection + contact-sheet handlers extracted from app.py.

Six free functions taking the :class:`ImgPlayerApp` as first arg —
they read / write app attributes (``_channel_selection``,
``_channel_layout_mode``, ``_channel_labels_visible``,
``_composite_geometry``, ``_last_contact_sheet_tiles``) and route
through ``app._cache``, ``app._controller``, ``app._window`` like
the original methods. Keeping them as free functions rather than a
mixin avoids the extra layer of class hierarchy for what is, at
heart, a bag of imperative side-effects on the app singleton.

The thin methods that remain on :class:`ImgPlayerApp` simply
delegate here — that way the existing signal connections (which bind
to ``self._on_channel_*`` etc.) keep working unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from img_player.annotate.overlay import widget_to_image
from img_player.render.contact_sheet import tile_at
from img_player.sequence.channels import ChannelSelection

if TYPE_CHECKING:
    from img_player.app import ImgPlayerApp


def set_channel_selection(app: ImgPlayerApp, selection: ChannelSelection) -> None:
    """Switch to a new channel selection (single + optional tiles).

    Drives both the cache (decode the union of all displayed
    channels in one OIIO call) and the display path (composite when
    contact-sheet mode is on).
    """
    app._channel_selection = selection
    union = list(selection.union_channels()) or None
    app._cache.set_channels(union)
    # Wipe the timeline's cache bar so we don't briefly show the
    # previous selection's runs while the prefetcher catches up.
    app._window.timeline.set_cached_frames(frozenset())
    if app._controller.sequence is None:
        return
    cur = app._controller.state.current_frame
    # Re-issue a prefetch around the playhead. ``controller.seek``
    # short-circuits when we ask for the frame we're already
    # parked on, so without this explicit call no decode would
    # ever fire after a Reset / Shift+C / double-click-isolate
    # — the user would have to scrub the timeline to see the new
    # selection take effect.
    app._cache.request_range(
        cur - 5, cur + 30, direction=app._controller.state.direction,
    )
    # Re-run the display pipeline against the (now stale) cache.
    # ``_on_frame_changed`` handles the miss correctly: it shows
    # the closest fallback frame if any and starts the wait-timer
    # poll so the freshly-decoded buffer lands the moment it's
    # ready. Without this call, the viewport would keep painting
    # the previous selection's composite until the next frame
    # change naturally triggered ``_on_frame_changed``.
    app._last_displayed = None  # force re-upload even if frame unchanged
    app._on_frame_changed(cur)


def on_channel_selection_changed(app: ImgPlayerApp, selection: object) -> None:
    """Apply a fresh :class:`ChannelSelection` from the transport menu.

    Both single-mode (no tiles) and contact-sheet mode flow through
    here so the cache and display path stay coherent — only the
    contact-sheet flag in ``_display_array`` differs at render time.
    """
    if not isinstance(selection, ChannelSelection):
        return  # signal carrier mismatch — defensive guard
    set_channel_selection(app, selection)


def on_tile_isolate_requested(
    app: ImgPlayerApp, widget_x: float, widget_y: float,
) -> None:
    """Double-click → isolate the clicked tile.

    Algorithm:

    1. Bail out if we're not in contact-sheet mode (no geometry).
    2. Convert widget coords → composite-image coords using the
       GL viewport's current transform (zoom factor + pan).
    3. Hit-test against the geometry. ``None`` = clicked in a gap
       or outside the composite bounds — we just ignore.
    4. Drive the channel menu so it (a) clears every tile checkbox,
       (b) sets the active radio to the hit label. That goes
       through the same persistence / signal path as a manual
       click, keeping the menu state coherent.
    """
    geometry = app._composite_geometry
    if geometry is None or not geometry.tiles:
        return
    gl = app._window.viewer.gl
    factor, pan_x, pan_y = gl.current_transform()
    if factor == 0.0:
        return
    ix, iy = widget_to_image(
        widget_xy=(widget_x, widget_y),
        widget_size=(gl.width(), gl.height()),
        img_size=gl.image_size(),
        factor=factor,
        pan=(pan_x, pan_y),
    )
    label = tile_at(geometry, int(ix), int(iy))
    if label is None:
        return
    # Save the current tile set first so Shift+C can restore it.
    _active, tiles, layout_mode, _labels = (
        app._window.transport.channel_menu_state()
    )
    if tiles:
        app._last_contact_sheet_tiles = tiles
    app._window.transport.restore_channel_state(label, (), layout_mode)
    app._window.set_status(f"Isolated channel: {label}")


def toggle_contact_sheet(app: ImgPlayerApp) -> None:
    """Shift+C handler — bascule single ⇄ contact-sheet.

    * Currently in contact-sheet → save the tile set, drop to
      single-mode on the active radio.
    * Currently in single → if we have a saved tile set, restore it;
      otherwise no-op (status message tells the user why).
    """
    active, tiles, layout_mode, _labels = (
        app._window.transport.channel_menu_state()
    )
    if tiles:
        # On → off: remember the tiles for the next toggle.
        app._last_contact_sheet_tiles = tiles
        app._window.transport.restore_channel_state(active, (), layout_mode)
        app._window.set_status("Contact sheet off")
        return
    if not app._last_contact_sheet_tiles:
        app._window.set_status(
            "Shift+C: nothing to restore — check tiles in the channel menu first"
        )
        return
    # Off → on: restore the saved set.
    app._window.transport.restore_channel_state(
        active, app._last_contact_sheet_tiles, layout_mode,
    )
    app._window.set_status("Contact sheet restored")


def on_channel_labels_visible_changed(app: ImgPlayerApp, on: object) -> None:
    """Footer "Show labels" checkbox toggled — refresh + persist.

    Pure display-time parameter (no decode involved): just stash the
    flag, persist, and re-run the display pipeline so the composite
    repaints with or without label chips.
    """
    if not isinstance(on, bool):
        return
    app._channel_labels_visible = on
    app._prefs.channel_labels_visible = on
    app._redisplay_current()


def on_channel_layout_mode_changed(app: ImgPlayerApp, mode: object) -> None:
    """Persist the contact-sheet grid mode and force a redisplay
    so the user sees the new layout immediately (without having to
    nudge the playhead)."""
    if not isinstance(mode, str):
        return
    app._channel_layout_mode = mode
    app._prefs.channel_layout_mode = mode
    # No cache invalidation: layout is purely a display-time
    # parameter, the composite repaints with the new shape.
    app._redisplay_current()
