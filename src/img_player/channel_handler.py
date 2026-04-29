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
    """Switch the FOCUSED layer's channel selection.

    The selection (active group + optional contact-sheet tiles)
    lives on the layer itself so that adding multiple sequences
    each preserves its own choice. The legacy ``app._channel_selection``
    attribute is kept in sync as a fallback for code paths that
    haven't been migrated yet (e.g. the export-time snapshot).

    Mutating the focused layer fires ``layer_modified`` → cache
    invalidates that layer's master range → the wired
    ``_refresh_after_stack_change`` re-issues prefetch + display.
    """
    focused = app._layer_stack.focused()
    app._channel_selection = selection  # legacy fallback
    if focused is None:
        # No sequence loaded yet — keep the selection on app state
        # so it's there for the first layer when it lands.
        return
    app._layer_stack.update(focused.id, channel_selection=selection)


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

    Per-layer setting on the focused layer. The app-level fallback
    + QSettings are kept in sync so freshly added layers inherit
    the user's last preference rather than reverting to the
    dataclass default.
    """
    if not isinstance(on, bool):
        return
    app._channel_labels_visible = on
    app._prefs.channel_labels_visible = on
    focused = app._layer_stack.focused()
    if focused is not None:
        # Display-time only — no cache invalidation. We bypass
        # ``stack.update`` (which would invalidate the layer range)
        # and mutate the field directly + redisplay.
        focused.channel_labels_visible = on
    app._redisplay_current()


def on_channel_layout_mode_changed(app: ImgPlayerApp, mode: object) -> None:
    """Per-layer contact-sheet grid mode + global preference snapshot.

    Like the labels-visible flag: bypasses ``stack.update`` because
    layout is purely a display-time parameter. Mutating the layer
    field directly skips the cache-invalidation path.
    """
    if not isinstance(mode, str):
        return
    app._channel_layout_mode = mode
    app._prefs.channel_layout_mode = mode
    focused = app._layer_stack.focused()
    if focused is not None:
        focused.channel_layout_mode = mode
    app._redisplay_current()
