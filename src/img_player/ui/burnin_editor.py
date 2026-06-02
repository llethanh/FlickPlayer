"""Editor dialog for :class:`BurninTemplate` — form-based properties
with a live preview.

Scope (MVP)
-----------

* Three-pane layout (template combo + toolbar above, preview on the
  left, element tree + properties on the right).
* Form-based positioning (anchor combo + offset spinboxes), NOT
  drag-on-preview yet. Drag would need the renderer to surface each
  element's rendered rect — a separate iteration.
* Save / Save As / Delete user templates. Builtins are read-only; the
  toolbar disables Save / Delete when a builtin is selected (Save As
  is always available to fork a builtin).
* "Set as active" pushes the slug back to the App so the running
  viewer reflects the just-edited template without a restart.
* Preview: the active template rendered onto a checker-pattern image
  (no live frame here — the user evaluates layout / typography, the
  live overlay shows the real frame).

State machine
-------------

* ``self._template`` — current working copy (frozen
  :class:`BurninTemplate`; mutations replace it with a new instance).
* ``self._current_slug`` — slug the template is loaded from (None
  for an in-memory unsaved one).
* ``self._dirty`` — set on every property change, cleared on Save.
* Save As prompts for a name, slugifies it, writes the user template.

Signals
-------

* :attr:`template_applied` — emitted with the slug the user just
  picked as active. The App connects to push it through
  :meth:`ImgPlayerApp.set_burnin_template_slug`.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

import numpy as np
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QImage, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from img_player.burnins.builtins import BUILTINS
from img_player.burnins.model import (
    BurninBar,
    BurninTemplate,
    ImageElement,
    SpacerElement,
    TextElement,
)
from img_player.burnins.renderer import render_burnin
from img_player.burnins.storage import (
    delete_shared_template,
    delete_user_template,
    is_shared_template,
    is_user_template,
    list_all_slugs,
    save_shared_template,
    save_user_template,
    shared_burnins_dir,
    slugify,
    template_for_slug,
)
from img_player.burnins.tokens import RenderContext, supported_tokens

log = logging.getLogger(__name__)


# Preview dimensions — render at 1080p so the bar pixel height (~65
# px at 6 %) gives the font-size scaler enough room to differentiate
# 12 pt from 14 pt from 18 pt. At 540p the scaler rounded every size
# in the 12-14 pt band to the same pixel and the user saw no change
# when nudging font_size. The widget itself scales the pixmap down
# to the dialog's actual size for display.
_PREVIEW_W = 1920
_PREVIEW_H = 1080


# Sample RenderContext used for the preview when no live context
# is available. Picks values that exercise every token so the user
# sees what each placeholder substitutes to.
_PREVIEW_CTX = RenderContext(
    frame=1042,
    frame_total=1244,
    fps=24.0,
    width=1920,
    height=1080,
    sequence="SH0010_Rendered.####.exr",
    layer_name="plate_v003",
    session_name="dailies_2026-05-27.session",
    date="2026-05-27",
    user="reviewer",
)


# ---------------------------------------------------------------------------
# Preview canvas
# ---------------------------------------------------------------------------

class _PreviewCanvas(QWidget):  # type: ignore[misc]
    """Renders the current template onto a checker-pattern background
    and paints it as a QPixmap. Re-renders on :meth:`update_preview`
    — the editor calls it after every property change.

    Also forwards click events: when the user clicks on the preview
    we hit-test against the element rects the renderer recorded and
    emit :attr:`element_clicked` with the ``(bar_id, element_idx)``
    of the element under the cursor. The editor wires this up to
    select the matching row in its element tree."""

    element_clicked = Signal(str, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(QSize(_PREVIEW_W // 2, _PREVIEW_H // 2))
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._template: BurninTemplate | None = None
        self._context = _PREVIEW_CTX
        self._pixmap: QPixmap | None = None
        # Pre-bake the checker background once — recomputed only on
        # resize. Acts as a "this is the image area" indicator so the
        # user sees where the burnin lands relative to the picture.
        self._checker = self._make_checker(_PREVIEW_W, _PREVIEW_H)
        # ``{(bar_id, elem_idx): (x, y, w, h)}`` in full-pixmap coords
        # — populated by the renderer via ``out_element_rects``. Used
        # for click hit-testing.
        self._element_rects: dict[tuple[str, int], tuple[int, int, int, int]] = {}

    def set_template(self, template: BurninTemplate | None) -> None:
        self._template = template
        self._rebuild_pixmap()

    def set_context(self, context: RenderContext) -> None:
        self._context = context
        self._rebuild_pixmap()

    def _rebuild_pixmap(self) -> None:
        base = self._checker.copy()
        self._element_rects = {}
        if self._template is not None:
            try:
                base = render_burnin(
                    base, self._template, self._context,
                    out_element_rects=self._element_rects,
                )
            except Exception:  # noqa: BLE001 — preview never crashes the editor
                log.exception("Burnin editor preview render failed")
        qimg = QImage(
            base.data, base.shape[1], base.shape[0],
            base.strides[0], QImage.Format.Format_RGBA8888,
        ).copy()
        self._pixmap = QPixmap.fromImage(qimg)
        self.update()

    # ---- Click-to-select ----------------------------------------------

    def _scaled_pixmap_rect(self) -> tuple[int, int, int, int] | None:
        """Where on the widget the scaled pixmap actually paints. Used
        by ``mousePressEvent`` to map widget-coord clicks back into
        pixmap coords for hit testing."""
        if self._pixmap is None:
            return None
        target = self.rect()
        scaled = self._pixmap.scaled(
            target.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (target.width() - scaled.width()) // 2
        y = (target.height() - scaled.height()) // 2
        return (x, y, scaled.width(), scaled.height())

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        rect = self._scaled_pixmap_rect()
        if rect is None or self._pixmap is None or not self._element_rects:
            return super().mousePressEvent(event)
        offset_x, offset_y, scaled_w, scaled_h = rect
        pos = event.position()
        cx, cy = pos.x() - offset_x, pos.y() - offset_y
        if cx < 0 or cy < 0 or cx >= scaled_w or cy >= scaled_h:
            return super().mousePressEvent(event)
        # Inverse scaling to map widget click → pixmap coord.
        pixmap_w = self._pixmap.width()
        pixmap_h = self._pixmap.height()
        px = cx * pixmap_w / scaled_w
        py = cy * pixmap_h / scaled_h
        # Hit-test the recorded rects. Use a generous inflate so very
        # small elements (single glyph) are still pickable.
        best: tuple[str, int] | None = None
        best_area = float("inf")
        for (bar_id, idx), (rx, ry, rw, rh) in self._element_rects.items():
            inflate = 6
            if (
                rx - inflate <= px <= rx + rw + inflate
                and ry - inflate <= py <= ry + rh + inflate
            ):
                area = max(1, rw) * max(1, rh)
                if area < best_area:
                    best = (bar_id, idx)
                    best_area = area
        if best is not None:
            self.element_clicked.emit(best[0], best[1])
        super().mousePressEvent(event)

    @staticmethod
    def _make_checker(w: int, h: int) -> np.ndarray:
        """16-square checker pattern as RGBA uint8. Same idiom as the
        viewer's transparency BG mode."""
        canvas = np.full((h, w, 4), (160, 160, 160, 255), dtype=np.uint8)
        step = 32
        for y in range(0, h, step):
            for x in range(0, w, step):
                if ((x // step) + (y // step)) % 2 == 0:
                    canvas[y:y + step, x:x + step, :3] = 110
        return canvas

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        try:
            if self._pixmap is None:
                return
            # Fit the pixmap into the widget rect while preserving its
            # aspect ratio — the dialog's splitter can be any size.
            target = self.rect()
            scaled = self._pixmap.scaled(
                target.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (target.width() - scaled.width()) // 2
            y = (target.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        finally:
            painter.end()


# ---------------------------------------------------------------------------
# Properties form
# ---------------------------------------------------------------------------

class _PropsForm(QWidget):  # type: ignore[misc]
    """Dynamic form pane — three nested pages (text / image / spacer)
    swapped via QStackedWidget on element-kind change. Emits a single
    ``changed`` signal whenever the user edits anything; the editor
    rebuilds the element + the parent template + the preview from the
    form's current values.

    The form is **deliberately stateless** wrt the model — every
    change calls ``self.values()`` and the editor rebuilds the
    template. Avoids the bug-prone "two-way binding" idiom.
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = "none"   # "text" | "image" | "spacer" | "none"
        self._stack = QStackedWidget(self)

        # Anchor + offsets — common to every element kind.
        self._anchor = QComboBox()
        self._anchor.addItems(["left", "center", "right"])
        self._offset_x = QSpinBox()
        self._offset_x.setRange(-2000, 2000)
        self._offset_y = QSpinBox()
        self._offset_y.setRange(-2000, 2000)

        # Text-specific.
        self._text = QLineEdit()
        # Tokens combo — clicking inserts the token at the cursor so
        # the user doesn't have to remember the {placeholder} syntax.
        self._tokens_combo = QComboBox()
        self._tokens_combo.addItem("Insert token…", "")
        for tok in supported_tokens():
            self._tokens_combo.addItem("{" + tok + "}", "{" + tok + "}")
        self._tokens_combo.activated.connect(self._on_token_insert)
        # Non-editable combo limited to the families the renderer's
        # font lookup map (``burnins.renderer._FONT_FILES``) carries
        # explicit Windows fallback chains for. Restricting the list
        # avoids the silent fallback to Pillow's bitmap default — the
        # symptom of "size / weight don't work" the user reported
        # when typing arbitrary family names. A template loaded with
        # a font NOT in this list is added dynamically on the fly
        # (see :meth:`load_element`) so the user's custom choice is
        # preserved even though it's not a preset.
        self._font_family = QComboBox()
        self._font_family.addItems([
            "Inter", "Segoe UI", "JetBrains Mono",
            "Consolas", "Arial",
        ])
        self._font_size = QSpinBox()
        self._font_size.setRange(4, 144)
        self._font_weight = QComboBox()
        self._font_weight.addItems(["normal", "bold"])
        # Text colour + opacity — split into a colour swatch (RGB
        # picker only) and a horizontal opacity slider. Same UX as
        # the bar controls so the user learns one pattern. State is
        # held in two attributes so the form can rebuild the
        # ``rgba(r, g, b, a)`` string when either changes.
        self._text_rgb: tuple[int, int, int] = (255, 229, 192)  # #FFE5C0 default
        self._text_alpha: int = 255
        self._text_color_btn = QPushButton()
        self._text_color_btn.setFixedSize(72, 24)
        self._text_color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._text_color_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._text_color_btn.clicked.connect(self._pick_text_color)
        self._text_opacity = self._make_text_opacity_slider()

        # Image-specific.
        self._image_path = QLineEdit()
        self._image_browse = QPushButton("…")
        self._image_browse.clicked.connect(self._pick_image)
        self._image_height_pct = QDoubleSpinBox()
        self._image_height_pct.setRange(0.05, 1.0)
        self._image_height_pct.setSingleStep(0.05)
        self._image_height_pct.setDecimals(2)

        # Spacer-specific.
        self._spacer_width = QSpinBox()
        self._spacer_width.setRange(0, 500)

        # ----- Build the stack -----
        # Page 0: empty (no selection).
        empty = QLabel("Select an element to edit its properties.")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setStyleSheet("color: #888; padding: 16px;")
        self._stack.addWidget(empty)

        # Page 1: text.
        text_page = QWidget()
        text_form = QFormLayout(text_page)
        text_form.addRow("Anchor:", self._anchor)
        text_form.addRow("Offset X (px):", self._offset_x)
        text_form.addRow("Offset Y (px):", self._offset_y)
        token_row = QHBoxLayout()
        token_row.addWidget(self._text, 1)
        token_row.addWidget(self._tokens_combo)
        text_form.addRow("Text:", token_row)
        text_form.addRow("Font family:", self._font_family)
        text_form.addRow("Font size (pt):", self._font_size)
        text_form.addRow("Font weight:", self._font_weight)
        color_row = QHBoxLayout()
        color_row.addWidget(self._text_color_btn)
        color_row.addSpacing(12)
        color_row.addWidget(QLabel("Opacity:"))
        color_row.addWidget(self._text_opacity, 1)
        text_form.addRow("Color:", color_row)
        self._stack.addWidget(text_page)

        # Page 2: image.
        image_page = QWidget()
        image_form = QFormLayout(image_page)
        image_form.addRow("Anchor:", self._anchor_proxy("img"))
        image_form.addRow("Offset X (px):", self._offset_x_proxy("img"))
        image_form.addRow("Offset Y (px):", self._offset_y_proxy("img"))
        path_row = QHBoxLayout()
        path_row.addWidget(self._image_path, 1)
        path_row.addWidget(self._image_browse)
        image_form.addRow("Image path:", path_row)
        image_form.addRow("Height (fraction of bar):", self._image_height_pct)
        self._stack.addWidget(image_page)

        # Page 3: spacer.
        spacer_page = QWidget()
        spacer_form = QFormLayout(spacer_page)
        spacer_form.addRow("Anchor:", self._anchor_proxy("sp"))
        spacer_form.addRow("Offset X (px):", self._offset_x_proxy("sp"))
        spacer_form.addRow("Offset Y (px):", self._offset_y_proxy("sp"))
        spacer_form.addRow("Width (px):", self._spacer_width)
        self._stack.addWidget(spacer_page)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

        # Wire change signals — every widget feeds the same ``changed``
        # so the editor's rebuild path is single-entry. The lambdas
        # discard the source signal's payload (``QSpinBox.valueChanged``
        # passes an int, ``QLineEdit.textChanged`` passes a str, etc.)
        # — ``changed`` is a zero-arg signal so connecting ``emit``
        # directly would crash with
        # ``TypeError: changed() only accepts 0 argument(s), 1 given``.
        def _fire(*_a):  # type: ignore[no-untyped-def]
            self.changed.emit()

        for w in (self._anchor, self._font_weight):
            w.currentIndexChanged.connect(_fire)
        for w in (
            self._offset_x, self._offset_y, self._font_size,
            self._spacer_width,
        ):
            w.valueChanged.connect(_fire)
        for w in (
            self._text, self._image_path,
        ):
            w.textChanged.connect(_fire)
        # Non-editable combo — only ``currentIndexChanged`` fires (no
        # free typing). Same downstream wiring as the other combos.
        self._font_family.currentIndexChanged.connect(_fire)
        self._image_height_pct.valueChanged.connect(_fire)

    # The QFormLayout widgets need to live in EXACTLY ONE parent. To
    # share the same anchor / offset widgets across three pages we'd
    # need to either reparent on page change or use proxies. Cleanest
    # is to keep one anchor combo per page — these proxies wire them
    # all to the same ``changed`` signal so we don't have to track
    # which page is active.
    def _anchor_proxy(self, suffix: str) -> QComboBox:
        c = QComboBox()
        c.addItems(["left", "center", "right"])
        c.currentIndexChanged.connect(lambda *_: self.changed.emit())
        setattr(self, f"_anchor_{suffix}", c)
        return c

    def _offset_x_proxy(self, suffix: str) -> QSpinBox:
        s = QSpinBox()
        s.setRange(-2000, 2000)
        s.valueChanged.connect(lambda *_: self.changed.emit())
        setattr(self, f"_offset_x_{suffix}", s)
        return s

    def _offset_y_proxy(self, suffix: str) -> QSpinBox:
        s = QSpinBox()
        s.setRange(-2000, 2000)
        s.valueChanged.connect(lambda *_: self.changed.emit())
        setattr(self, f"_offset_y_{suffix}", s)
        return s

    # ---- Color pick / image browse helpers --------------------------

    def _make_text_opacity_slider(self):  # type: ignore[no-untyped-def]
        """Slider + percent readout pair editing only the text
        element's alpha channel — same UX as the bar opacity sliders."""
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        slider.setSingleStep(1)
        slider.setPageStep(10)
        slider.setMinimumWidth(120)
        slider.setToolTip(
            "Text opacity (alpha channel). "
            "0 % = fully transparent, 100 % = opaque.",
        )
        readout = QLabel("100 %")
        readout.setMinimumWidth(40)
        readout.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        slider.valueChanged.connect(
            lambda v, lb=readout: lb.setText(f"{int(v)} %"),
        )
        slider.valueChanged.connect(self._on_text_opacity_changed)
        return _SliderRow(slider, readout)

    def _set_text_swatch(self) -> None:
        """Repaint the text colour button to show the current
        ``self._text_rgb`` + ``self._text_alpha``."""
        r, g, b = self._text_rgb
        rgba = f"rgba({r}, {g}, {b}, {self._text_alpha / 255.0:.2f})"
        self._text_color_btn.setStyleSheet(
            f"QPushButton {{ background-color: {rgba}; "
            f"border: 1px solid #555; border-radius: 3px; }}"
        )
        self._text_color_btn.setToolTip(
            f"{rgba}  —  click to change text colour",
        )

    def _pick_text_color(self) -> None:
        """Open an RGB-only colour picker for the text element. The
        alpha stays where the opacity slider has it."""
        from PySide6.QtGui import QColor  # noqa: PLC0415
        r, g, b = self._text_rgb
        chosen = QColorDialog.getColor(
            QColor(r, g, b), self, "Pick text colour",
        )
        if not chosen.isValid():
            return
        self._text_rgb = (chosen.red(), chosen.green(), chosen.blue())
        self._set_text_swatch()
        self.changed.emit()

    def _on_text_opacity_changed(self, v: int) -> None:
        self._text_alpha = max(0, min(255, int(round(v / 100.0 * 255))))
        self._set_text_swatch()
        self.changed.emit()

    def _pick_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick image",
            self._image_path.text() or "",
            "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp);;All files (*)",
        )
        if path:
            self._image_path.setText(path)
            self.changed.emit()

    def _on_token_insert(self, index: int) -> None:
        # ``index 0`` = the placeholder "Insert token…" — no-op.
        if index <= 0:
            return
        token = self._tokens_combo.itemData(index)
        if token:
            self._text.insert(token)
        # Reset to the placeholder so the next pick fires another
        # activated signal cleanly.
        self._tokens_combo.setCurrentIndex(0)

    # ---- Public API -------------------------------------------------

    def load_element(self, elem) -> None:  # type: ignore[no-untyped-def]
        """Populate the form from ``elem``. Blocks signals so loading
        doesn't fire a spurious ``changed``."""
        blocker = _BlockSignals(self)
        with blocker:
            if isinstance(elem, TextElement):
                self._kind = "text"
                self._stack.setCurrentIndex(1)
                self._anchor.setCurrentText(elem.anchor)
                self._offset_x.setValue(int(elem.offset_x))
                self._offset_y.setValue(int(elem.offset_y))
                self._text.setText(elem.text)
                # If the template's font isn't in our preset list
                # (= a custom template hand-edited or imported), add
                # it on the fly so ``setCurrentText`` picks it up
                # rather than silently snapping to the first item.
                if self._font_family.findText(elem.font_family) < 0:
                    self._font_family.addItem(elem.font_family)
                self._font_family.setCurrentText(elem.font_family)
                self._font_size.setValue(int(elem.font_size_pt))
                self._font_weight.setCurrentText(elem.font_weight)
                # Parse colour into RGB + alpha-byte state. The
                # opacity slider and the swatch read from these.
                from img_player.burnins.renderer import _parse_color  # noqa: PLC0415
                r, g, b, a = _parse_color(elem.color)
                self._text_rgb = (r, g, b)
                self._text_alpha = a
                self._text_opacity.setValue(int(round(a / 255.0 * 100)))
                self._set_text_swatch()
            elif isinstance(elem, ImageElement):
                self._kind = "image"
                self._stack.setCurrentIndex(2)
                self._anchor_img.setCurrentText(elem.anchor)
                self._offset_x_img.setValue(int(elem.offset_x))
                self._offset_y_img.setValue(int(elem.offset_y))
                self._image_path.setText(elem.path)
                self._image_height_pct.setValue(float(elem.height_pct))
            elif isinstance(elem, SpacerElement):
                self._kind = "spacer"
                self._stack.setCurrentIndex(3)
                self._anchor_sp.setCurrentText(elem.anchor)
                self._offset_x_sp.setValue(int(elem.offset_x))
                self._offset_y_sp.setValue(int(elem.offset_y))
                self._spacer_width.setValue(int(elem.width_px))
            else:
                self._kind = "none"
                self._stack.setCurrentIndex(0)

    def to_element(self):  # type: ignore[no-untyped-def]
        """Build a fresh element instance from the form's current
        values. Returns ``None`` when no kind is selected."""
        if self._kind == "text":
            r, g, b = self._text_rgb
            color_str = (
                f"rgba({r}, {g}, {b}, {self._text_alpha / 255.0:.2f})"
            )
            return TextElement(
                anchor=self._anchor.currentText(),
                offset_x=float(self._offset_x.value()),
                offset_y=float(self._offset_y.value()),
                text=self._text.text(),
                font_family=self._font_family.currentText() or "Inter",
                font_size_pt=int(self._font_size.value()),
                font_weight=self._font_weight.currentText(),
                color=color_str,
            )
        if self._kind == "image":
            return ImageElement(
                anchor=self._anchor_img.currentText(),
                offset_x=float(self._offset_x_img.value()),
                offset_y=float(self._offset_y_img.value()),
                path=self._image_path.text(),
                height_pct=float(self._image_height_pct.value()),
            )
        if self._kind == "spacer":
            return SpacerElement(
                anchor=self._anchor_sp.currentText(),
                offset_x=float(self._offset_x_sp.value()),
                offset_y=float(self._offset_y_sp.value()),
                width_px=int(self._spacer_width.value()),
            )
        return None


class _SliderRow(QWidget):  # type: ignore[misc]
    """Slider + percent readout in a single horizontal container, with
    a QSpinBox-shaped ``value() / setValue() / blockSignals`` API so
    callers can swap a QSpinBox for one without changing call sites."""

    def __init__(
        self, slider: QSlider, readout: QLabel,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._slider = slider
        self._readout = readout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(slider, 1)
        layout.addWidget(readout)

    def value(self) -> int:
        return int(self._slider.value())

    def setValue(self, v: int) -> None:  # noqa: N802 — match Qt naming
        self._slider.setValue(int(v))
        self._readout.setText(f"{int(v)} %")

    def blockSignals(self, on: bool) -> bool:  # noqa: N802
        prev = super().blockSignals(on)
        self._slider.blockSignals(on)
        return prev


class _BlockSignals:
    """Tiny ctx mgr that blocks all signals on a widget tree — used
    when populating the form from an element so we don't fire
    ``changed`` for every spinbox load."""

    def __init__(self, root: QWidget) -> None:
        self._root = root
        self._widgets: list[QWidget] = []

    def __enter__(self) -> "_BlockSignals":
        self._widgets = [
            w for w in self._root.findChildren(QWidget)
            if w.signalsBlocked() is False
        ]
        for w in self._widgets:
            w.blockSignals(True)
        return self

    def __exit__(self, *_a: Any) -> None:
        for w in self._widgets:
            w.blockSignals(False)


# ---------------------------------------------------------------------------
# Editor dialog
# ---------------------------------------------------------------------------

class BurninEditorDialog(QDialog):  # type: ignore[misc]
    """Top-level editor dialog. One instance owned by the App; reused
    across opens so the user's pane sizes / window position carry."""

    template_applied = Signal(str)
    """Emitted with the active template's slug when the user clicks
    "Set as active". The App connects this to
    :meth:`ImgPlayerApp.set_burnin_template_slug`."""

    shared_dir_changed = Signal(str)
    """Emitted when the user picks a new shared-burnin-templates
    directory via the "Shared folder…" button (or clears it). The
    App connects this to a slot that persists the path to prefs
    and re-renders the View → Active burnin template submenu."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Burnin template editor")
        self.resize(1200, 680)

        # State
        self._template: BurninTemplate = template_for_slug("default")
        self._current_slug: str | None = "default"
        self._dirty = False

        # ---- Top toolbar -----------------------------------------
        self._template_combo = QComboBox()
        self._template_combo.setMinimumWidth(220)
        self._template_combo.currentIndexChanged.connect(
            self._on_template_picked,
        )
        self._save_btn = QPushButton("Save")
        self._save_btn.clicked.connect(self._on_save)
        self._save_as_btn = QPushButton("Save As…")
        self._save_as_btn.clicked.connect(self._on_save_as)
        self._delete_btn = QPushButton("Delete")
        self._delete_btn.clicked.connect(self._on_delete)
        self._apply_btn = QPushButton("Set as active")
        self._apply_btn.clicked.connect(self._on_apply)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Template:"))
        toolbar.addWidget(self._template_combo)
        toolbar.addStretch(1)
        toolbar.addWidget(self._save_btn)
        toolbar.addWidget(self._save_as_btn)
        toolbar.addWidget(self._delete_btn)
        toolbar.addSpacing(12)
        toolbar.addWidget(self._apply_btn)

        # ---- Shared-library row -----------------------------------
        # Lets the user point the editor at a folder (typically a
        # network share) so the whole team converges on the same
        # template library. Templates discovered there show up in
        # the combo alongside local user templates and the builtin.
        # See :mod:`img_player.burnins.storage` for the lookup
        # precedence (user > shared > builtin).
        self._shared_label = QLabel("Shared library:")
        self._shared_path_edit = QLineEdit()
        self._shared_path_edit.setReadOnly(True)
        self._shared_path_edit.setPlaceholderText(
            "(not configured — click Browse… to share with the team)",
        )
        self._shared_browse_btn = QPushButton("Browse…")
        self._shared_browse_btn.clicked.connect(
            self._on_shared_browse,
        )
        self._shared_clear_btn = QPushButton("Clear")
        self._shared_clear_btn.clicked.connect(self._on_shared_clear)

        shared_row = QHBoxLayout()
        shared_row.addWidget(self._shared_label)
        shared_row.addWidget(self._shared_path_edit, 1)
        shared_row.addWidget(self._shared_browse_btn)
        shared_row.addWidget(self._shared_clear_btn)

        # ---- Splitter --------------------------------------------
        self._preview = _PreviewCanvas()
        # Click on a rendered element → select the matching row in
        # the element tree. The renderer populates the rect dict; the
        # canvas hit-tests + emits this signal.
        self._preview.element_clicked.connect(self._on_preview_element_clicked)
        right = self._build_right_pane()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._preview)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        # ---- Bottom button box ----------------------------------
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addLayout(toolbar)
        layout.addLayout(shared_row)
        layout.addWidget(splitter, 1)
        layout.addWidget(buttons)

        # Populate the combo + initial state. Reading the shared dir
        # here (rather than in the App) keeps the editor self-
        # initialising: it asks storage for whatever path is
        # currently registered via ``set_shared_dir_provider``.
        self._sync_shared_path_field()
        self._refresh_template_combo()
        self._sync_ui_to_template()

    # ---------------- Right pane -----------------------------------

    def _build_right_pane(self) -> QWidget:
        # Element tree.
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.itemSelectionChanged.connect(self._on_tree_selection)
        # Element-toolbar.
        self._add_btn = QToolButton()
        self._add_btn.setText("+ Add ▾")
        self._add_btn.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup,
        )
        add_menu = QMenu(self._add_btn)
        add_menu.addAction("Text", lambda: self._add_element("text"))
        add_menu.addAction("Image", lambda: self._add_element("image"))
        add_menu.addAction("Spacer", lambda: self._add_element("spacer"))
        self._add_btn.setMenu(add_menu)
        self._remove_btn = QPushButton("Remove")
        self._remove_btn.clicked.connect(self._on_remove_element)
        self._up_btn = QPushButton("↑")
        self._up_btn.clicked.connect(lambda: self._move_element(-1))
        self._down_btn = QPushButton("↓")
        self._down_btn.clicked.connect(lambda: self._move_element(+1))

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._add_btn)
        btn_row.addWidget(self._remove_btn)
        btn_row.addSpacing(8)
        btn_row.addWidget(self._up_btn)
        btn_row.addWidget(self._down_btn)
        btn_row.addStretch(1)

        # Bar toggles + height controls. Height is shown in PERCENT of
        # image height (1.0 .. 50.0 %) — internally the template stores
        # the same value as a 0..1 fraction. The form widgets handle
        # the conversion in :meth:`_on_top_bar_height` /
        # :meth:`_on_bottom_bar_height`. Range floor of 1 % is well
        # below any usable size; ceiling of 50 % means at most half
        # the image is bar, which is already excessive.
        self._top_bar_enabled = QCheckBox("Top bar")
        self._top_bar_enabled.toggled.connect(self._on_top_bar_toggled)
        self._top_bar_height = QDoubleSpinBox()
        self._top_bar_height.setRange(1.0, 50.0)
        self._top_bar_height.setSingleStep(0.5)
        self._top_bar_height.setDecimals(1)
        self._top_bar_height.setSuffix(" %")
        self._top_bar_height.setToolTip(
            "Bar height as a percentage of the image height "
            "(6 % is the default — about 65 px on a 1080-line image).",
        )
        self._top_bar_height.valueChanged.connect(self._on_top_bar_height)

        self._bottom_bar_enabled = QCheckBox("Bottom bar")
        self._bottom_bar_enabled.toggled.connect(self._on_bottom_bar_toggled)
        self._bottom_bar_height = QDoubleSpinBox()
        self._bottom_bar_height.setRange(1.0, 50.0)
        self._bottom_bar_height.setSingleStep(0.5)
        self._bottom_bar_height.setDecimals(1)
        self._bottom_bar_height.setSuffix(" %")
        self._bottom_bar_height.setToolTip(
            "Bar height as a percentage of the image height.",
        )
        self._bottom_bar_height.valueChanged.connect(self._on_bottom_bar_height)

        # Colour + opacity controls — split deliberately. The colour
        # swatch opens an RGB-only picker (no alpha) so the user can
        # focus on hue / saturation; the opacity spinbox edits only
        # the alpha channel. Both feed back into the same
        # ``rgba(r, g, b, a)`` string the renderer / theme already use,
        # so the on-disk format stays compatible with hand-edited
        # templates.
        self._top_bar_color_btn = self._make_color_swatch("top")
        self._bottom_bar_color_btn = self._make_color_swatch("bottom")
        self._top_bar_opacity = self._make_opacity_spinbox("top")
        self._bottom_bar_opacity = self._make_opacity_spinbox("bottom")

        # Lay out as:
        #   [☑ Top bar] Height: [6.0 %]  Color: [swatch]  Opacity: [85 %]
        bars_form = QFormLayout()
        top_row = QHBoxLayout()
        top_row.addWidget(self._top_bar_enabled)
        top_row.addStretch(1)
        top_row.addWidget(QLabel("Height:"))
        top_row.addWidget(self._top_bar_height)
        top_row.addSpacing(12)
        top_row.addWidget(QLabel("Color:"))
        top_row.addWidget(self._top_bar_color_btn)
        top_row.addSpacing(12)
        top_row.addWidget(QLabel("Opacity:"))
        top_row.addWidget(self._top_bar_opacity)
        bot_row = QHBoxLayout()
        bot_row.addWidget(self._bottom_bar_enabled)
        bot_row.addStretch(1)
        bot_row.addWidget(QLabel("Height:"))
        bot_row.addWidget(self._bottom_bar_height)
        bot_row.addSpacing(12)
        bot_row.addWidget(QLabel("Color:"))
        bot_row.addWidget(self._bottom_bar_color_btn)
        bot_row.addSpacing(12)
        bot_row.addWidget(QLabel("Opacity:"))
        bot_row.addWidget(self._bottom_bar_opacity)
        bars_form.addRow(top_row)
        bars_form.addRow(bot_row)

        # Properties form.
        self._props = _PropsForm()
        self._props.changed.connect(self._on_props_changed)

        right = QWidget()
        v = QVBoxLayout(right)
        v.addLayout(bars_form)
        v.addWidget(QLabel("Elements:"))
        v.addWidget(self._tree, 1)
        v.addLayout(btn_row)
        v.addWidget(QLabel("Properties:"))
        v.addWidget(self._props, 1)
        return right

    # ---------------- Template combo ------------------------------

    def _refresh_template_combo(self) -> None:
        """Rebuild the template combo from current user templates +
        builtins. Preserves the user's current selection if the slug
        still exists; otherwise picks the first available."""
        blocker = self._template_combo.blockSignals(True)
        try:
            keep = self._current_slug
            self._template_combo.clear()
            for slug in list_all_slugs():
                label = self._label_for_slug(slug)
                self._template_combo.addItem(label, slug)
            # Restore selection.
            if keep is not None:
                idx = self._template_combo.findData(keep)
                if idx >= 0:
                    self._template_combo.setCurrentIndex(idx)
        finally:
            self._template_combo.blockSignals(blocker)

    @staticmethod
    def _label_for_slug(slug: str) -> str:
        # Show a "(user)" / "(shared)" / "(builtin)" hint so the
        # user sees which storage tier each slug belongs to without
        # consulting a separate list. Precedence (user > shared >
        # builtin) means we tag based on the highest-priority
        # source — a "user" tag implies "this is your local copy
        # whether or not the team has one".
        is_user = is_user_template(slug)
        is_shared = is_shared_template(slug)
        is_builtin = slug in BUILTINS
        if is_user and is_builtin:
            return f"{slug} (user, shadows builtin)"
        if is_user:
            return f"{slug} (user)"
        if is_shared:
            return f"{slug} (shared)"
        return f"{slug} (builtin)"

    def _on_template_picked(self, idx: int) -> None:
        if idx < 0:
            return
        slug = self._template_combo.itemData(idx)
        if not slug or slug == self._current_slug:
            return
        if self._dirty and not self._confirm_discard_changes():
            # Snap the combo back to the previous slug.
            blocker = self._template_combo.blockSignals(True)
            try:
                prev = self._template_combo.findData(self._current_slug)
                if prev >= 0:
                    self._template_combo.setCurrentIndex(prev)
            finally:
                self._template_combo.blockSignals(blocker)
            return
        self._template = template_for_slug(slug)
        self._current_slug = slug
        self._dirty = False
        self._sync_ui_to_template()

    def _confirm_discard_changes(self) -> bool:
        ret = QMessageBox.question(
            self, "Unsaved changes",
            "Switching templates will discard your unsaved changes. Continue?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return ret == QMessageBox.StandardButton.Discard

    # ---------------- Tree -----------------------------------------

    def _sync_ui_to_template(self) -> None:
        """Push the current ``self._template`` into every widget. Called
        on template-pick, on bar add/remove, and on element-list
        changes. Does NOT re-emit the form's ``changed`` signal."""
        # Bar toggles.
        for cb, val in (
            (self._top_bar_enabled, self._template.top_bar.enabled),
            (self._bottom_bar_enabled, self._template.bottom_bar.enabled),
        ):
            blocker = cb.blockSignals(True)
            try:
                cb.setChecked(bool(val))
            finally:
                cb.blockSignals(blocker)
        for sb, val in (
            (self._top_bar_height, self._template.top_bar.height_pct),
            (self._bottom_bar_height, self._template.bottom_bar.height_pct),
        ):
            blocker = sb.blockSignals(True)
            try:
                # Spinbox shows percent (6.0); template stores fraction
                # (0.06). Multiply on read, divide on write.
                sb.setValue(float(val) * 100.0)
            finally:
                sb.blockSignals(blocker)
        # Colour swatches + opacity spinboxes.
        from img_player.burnins.renderer import _parse_color  # noqa: PLC0415
        for bar, swatch, opacity in (
            (
                self._template.top_bar,
                self._top_bar_color_btn,
                self._top_bar_opacity,
            ),
            (
                self._template.bottom_bar,
                self._bottom_bar_color_btn,
                self._bottom_bar_opacity,
            ),
        ):
            self._set_swatch_color(swatch, bar.bg_color)
            _, _, _, a_byte = _parse_color(bar.bg_color)
            blocker = opacity.blockSignals(True)
            try:
                opacity.setValue(int(round(a_byte / 255.0 * 100)))
            finally:
                opacity.blockSignals(blocker)

        # Tree rebuild.
        self._rebuild_tree()
        # Preview.
        self._preview.set_template(self._template)
        # Enable/disable per-slug toolbar entries.
        self._update_toolbar_state()

    def _rebuild_tree(self) -> None:
        sel = self._current_selection_path()
        blocker = self._tree.blockSignals(True)
        try:
            self._tree.clear()
            top = QTreeWidgetItem(["Top bar"])
            top.setData(0, Qt.ItemDataRole.UserRole, ("bar", "top"))
            for i, elem in enumerate(self._template.top_bar.elements):
                child = QTreeWidgetItem([self._summarise_element(elem)])
                child.setData(0, Qt.ItemDataRole.UserRole, ("elem", "top", i))
                top.addChild(child)
            self._tree.addTopLevelItem(top)
            top.setExpanded(True)

            bot = QTreeWidgetItem(["Bottom bar"])
            bot.setData(0, Qt.ItemDataRole.UserRole, ("bar", "bottom"))
            for i, elem in enumerate(self._template.bottom_bar.elements):
                child = QTreeWidgetItem([self._summarise_element(elem)])
                child.setData(0, Qt.ItemDataRole.UserRole, ("elem", "bottom", i))
                bot.addChild(child)
            self._tree.addTopLevelItem(bot)
            bot.setExpanded(True)
        finally:
            self._tree.blockSignals(blocker)
        # Restore selection if possible.
        if sel is not None:
            self._select_path(sel)

    def _summarise_element(self, elem) -> str:  # type: ignore[no-untyped-def]
        if isinstance(elem, TextElement):
            preview = elem.text if len(elem.text) < 40 else elem.text[:37] + "…"
            return f"text · {elem.anchor} · {preview}"
        if isinstance(elem, ImageElement):
            tail = elem.path.split("/")[-1].split("\\")[-1] or "(no path)"
            return f"image · {elem.anchor} · {tail}"
        if isinstance(elem, SpacerElement):
            return f"spacer · {elem.anchor} · {elem.width_px}px"
        return repr(elem)

    def _current_selection_path(self):  # type: ignore[no-untyped-def]
        items = self._tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, Qt.ItemDataRole.UserRole)

    def _select_path(self, path) -> None:  # type: ignore[no-untyped-def]
        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            bar = root.child(i)
            if bar.data(0, Qt.ItemDataRole.UserRole) == path:
                self._tree.setCurrentItem(bar)
                return
            for j in range(bar.childCount()):
                child = bar.child(j)
                if child.data(0, Qt.ItemDataRole.UserRole) == path:
                    self._tree.setCurrentItem(child)
                    return

    def _on_tree_selection(self) -> None:
        path = self._current_selection_path()
        if path is None or path[0] == "bar":
            self._props.load_element(None)
            return
        _, bar_id, idx = path
        bar = self._template.top_bar if bar_id == "top" else self._template.bottom_bar
        if 0 <= idx < len(bar.elements):
            self._props.load_element(bar.elements[idx])

    def _on_preview_element_clicked(self, bar_id: str, idx: int) -> None:
        """Click on a rendered element in the preview → select the
        matching tree row so the properties pane loads its values
        (mirrors the tree's own selection-change path)."""
        self._select_path(("elem", bar_id, idx))

    # ---------------- Mutations -----------------------------------

    def _replace_template(self, new: BurninTemplate) -> None:
        self._template = new
        self._dirty = True
        self._preview.set_template(new)
        self._update_toolbar_state()

    def _replace_bar(self, bar_id: str, new_bar: BurninBar) -> None:
        if bar_id == "top":
            self._replace_template(replace(self._template, top_bar=new_bar))
        else:
            self._replace_template(replace(self._template, bottom_bar=new_bar))

    def _on_top_bar_toggled(self, on: bool) -> None:
        self._replace_bar(
            "top",
            replace(self._template.top_bar, enabled=bool(on)),
        )

    def _on_bottom_bar_toggled(self, on: bool) -> None:
        self._replace_bar(
            "bottom",
            replace(self._template.bottom_bar, enabled=bool(on)),
        )

    # ---------------- Bar colour swatches --------------------------

    def _make_color_swatch(self, slot: str) -> QPushButton:
        """Build a flat-coloured button that opens the colour picker
        for the given bar slot ("top" or "bottom"). The button's
        stylesheet is refreshed by :meth:`_set_swatch_color` whenever
        the underlying bar's ``bg_color`` changes."""
        btn = QPushButton()
        btn.setFixedSize(72, 24)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.clicked.connect(lambda *_: self._pick_bar_color(slot))
        return btn

    def _set_swatch_color(self, btn: QPushButton, color_str: str) -> None:
        """Repaint a swatch button to show ``color_str`` (any string
        the renderer's parser accepts — ``#RGB``, ``#RGBA``, or the
        CSS ``rgba(r, g, b, a)`` form). The QSS gracefully blends
        alpha against the dialog's grey background, so a 0.85-alpha
        bar reads as semi-transparent, not solid."""
        btn.setStyleSheet(
            f"QPushButton {{ background-color: {color_str}; "
            f"border: 1px solid #555; border-radius: 3px; }}"
        )
        btn.setToolTip(
            f"{color_str}  —  click to change colour / opacity",
        )

    def _make_opacity_spinbox(self, slot: str):  # type: ignore[no-untyped-def]
        """A horizontal slider (0 .. 100 %) paired with a tiny live
        readout label, editing only the alpha channel of the bar's
        ``bg_color``. Returned as a single container widget so the
        caller can drop it into a row layout transparently.

        Slider rather than spinbox: dragging gives a continuous
        preview of the transparency, which is much more intuitive
        than typing percentages — the bar fades in / out in real time
        under the cursor.

        The container exposes ``value() / setValue() / setBlockSignals``
        so callers that previously talked to a QSpinBox keep working.
        """
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        slider.setSingleStep(1)
        slider.setPageStep(10)
        slider.setMinimumWidth(120)
        slider.setToolTip(
            "Bar background opacity (alpha channel). "
            "0 % = fully transparent, 100 % = opaque.",
        )
        readout = QLabel("100 %")
        readout.setMinimumWidth(40)
        readout.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        slider.valueChanged.connect(
            lambda v, lb=readout: lb.setText(f"{int(v)} %"),
        )
        slider.valueChanged.connect(
            lambda v, s=slot: self._on_bar_opacity_changed(s, int(v)),
        )

        # Container widget — exposes the slider's value API so the
        # rest of the editor can ``opacity.setValue`` / ``.value()``
        # without knowing about the wrapper.
        container = _SliderRow(slider, readout)
        return container

    def _pick_bar_color(self, slot: str) -> None:
        """Open a colour-only QColorDialog (no alpha channel), then
        push the picked RGB back into the template while PRESERVING
        the bar's current opacity. Alpha lives in the dedicated
        opacity spinbox so the two adjustments don't fight each other."""
        from PySide6.QtGui import QColor  # noqa: PLC0415
        from img_player.burnins.renderer import _parse_color  # noqa: PLC0415

        bar = self._template.top_bar if slot == "top" else self._template.bottom_bar
        # Parse the bar's current colour through the renderer's own
        # parser so the picker opens on EXACTLY the live colour rather
        # than QColor's narrower ``#RGB`` parsing (which doesn't know
        # ``rgba(...)``).
        r, g, b, a = _parse_color(bar.bg_color)
        initial = QColor(r, g, b)   # opacity stays in the spinbox
        chosen = QColorDialog.getColor(
            initial, self, "Pick bar colour",
        )
        if not chosen.isValid():
            return
        # Preserve the existing alpha — the opacity spinbox is the
        # canonical source of truth for the transparency.
        rgba = self._format_rgba(chosen.red(), chosen.green(), chosen.blue(), a)
        self._replace_bar(slot, replace(bar, bg_color=rgba))
        btn = (
            self._top_bar_color_btn if slot == "top"
            else self._bottom_bar_color_btn
        )
        self._set_swatch_color(btn, rgba)

    def _on_bar_opacity_changed(self, slot: str, value: int) -> None:
        """Opacity spinbox callback — updates only the alpha channel
        of the bar's ``bg_color``, leaving RGB untouched."""
        from img_player.burnins.renderer import _parse_color  # noqa: PLC0415

        bar = self._template.top_bar if slot == "top" else self._template.bottom_bar
        r, g, b, _ = _parse_color(bar.bg_color)
        # Spinbox is 0..100 percent → renderer expects 0..255 alpha.
        new_alpha = max(0, min(255, int(round(value / 100.0 * 255))))
        rgba = self._format_rgba(r, g, b, new_alpha)
        self._replace_bar(slot, replace(bar, bg_color=rgba))
        btn = (
            self._top_bar_color_btn if slot == "top"
            else self._bottom_bar_color_btn
        )
        self._set_swatch_color(btn, rgba)

    @staticmethod
    def _format_rgba(r: int, g: int, b: int, a_byte: int) -> str:
        """Canonical RGBA string for templates — alpha as a 0..1 float
        (matches the theme.py + builtin templates convention)."""
        return f"rgba({int(r)}, {int(g)}, {int(b)}, {a_byte / 255.0:.2f})"

    def _on_top_bar_height(self, value: float) -> None:
        # Spinbox value is in PERCENT (6.0 == 6 %); template stores
        # the same as a 0..1 fraction.
        self._replace_bar(
            "top",
            replace(self._template.top_bar, height_pct=float(value) / 100.0),
        )

    def _on_bottom_bar_height(self, value: float) -> None:
        self._replace_bar(
            "bottom",
            replace(
                self._template.bottom_bar,
                height_pct=float(value) / 100.0,
            ),
        )

    def _on_props_changed(self) -> None:
        path = self._current_selection_path()
        if path is None or path[0] != "elem":
            return
        _, bar_id, idx = path
        elem = self._props.to_element()
        if elem is None:
            return
        bar = self._template.top_bar if bar_id == "top" else self._template.bottom_bar
        elements = list(bar.elements)
        if 0 <= idx < len(elements):
            elements[idx] = elem
            new_bar = replace(bar, elements=tuple(elements))
            self._replace_bar(bar_id, new_bar)
            # Refresh the tree row's label (the summary may have changed).
            item = self._tree.currentItem()
            if item is not None:
                item.setText(0, self._summarise_element(elem))

    def _add_element(self, kind: str) -> None:
        # Add to the bar of the currently-selected node, defaulting to
        # top bar when the selection is on something else.
        path = self._current_selection_path()
        if path and path[0] == "bar":
            bar_id = path[1]
        elif path and path[0] == "elem":
            bar_id = path[1]
        else:
            bar_id = "top"
        bar = self._template.top_bar if bar_id == "top" else self._template.bottom_bar
        if kind == "text":
            new_elem = TextElement(text="{frame}/{frame_total}")
        elif kind == "image":
            new_elem = ImageElement(path="")
        else:
            new_elem = SpacerElement()
        new_bar = replace(bar, elements=(*bar.elements, new_elem))
        self._replace_bar(bar_id, new_bar)
        self._rebuild_tree()
        # Select the new element so the props pane populates.
        self._select_path(("elem", bar_id, len(new_bar.elements) - 1))

    def _on_remove_element(self) -> None:
        path = self._current_selection_path()
        if not path or path[0] != "elem":
            return
        _, bar_id, idx = path
        bar = self._template.top_bar if bar_id == "top" else self._template.bottom_bar
        elements = list(bar.elements)
        if 0 <= idx < len(elements):
            del elements[idx]
            new_bar = replace(bar, elements=tuple(elements))
            self._replace_bar(bar_id, new_bar)
            self._rebuild_tree()

    def _move_element(self, delta: int) -> None:
        path = self._current_selection_path()
        if not path or path[0] != "elem":
            return
        _, bar_id, idx = path
        bar = self._template.top_bar if bar_id == "top" else self._template.bottom_bar
        elements = list(bar.elements)
        new_idx = idx + delta
        if not (0 <= new_idx < len(elements)):
            return
        elements[idx], elements[new_idx] = elements[new_idx], elements[idx]
        new_bar = replace(bar, elements=tuple(elements))
        self._replace_bar(bar_id, new_bar)
        self._rebuild_tree()
        self._select_path(("elem", bar_id, new_idx))

    # ---------------- Save / load / apply --------------------------

    def _update_toolbar_state(self) -> None:
        slug = self._current_slug
        # Save / Delete operate on any template the user owns —
        # either a local user template or a shared (team) template.
        # Builtins are read-only; Save As always works (= fork to a
        # new slug).
        editable = bool(slug) and (
            is_user_template(slug or "")
            or is_shared_template(slug or "")
        )
        self._save_btn.setEnabled(editable)
        self._delete_btn.setEnabled(editable)

    # ---------------- Shared library -------------------------------

    def _sync_shared_path_field(self) -> None:
        """Reflect the currently-configured shared directory in the
        toolbar text field + Clear button enablement. Read live
        from storage so the field doesn't get stale when the App
        rewires the provider."""
        current = shared_burnins_dir()
        text = str(current) if current is not None else ""
        # No need to block signals — the field is read-only and our
        # handler is only triggered by Browse… / Clear clicks.
        self._shared_path_edit.setText(text)
        self._shared_clear_btn.setEnabled(bool(text))

    def _on_shared_browse(self) -> None:
        """Open a directory picker for the shared library. The chosen
        path goes to prefs (via the ``shared_dir_changed`` signal the
        App listens to) and the combo is refreshed so any newly-
        visible shared templates surface immediately."""
        current = self._shared_path_edit.text()
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Pick the shared burnin templates folder",
            current or "",
        )
        if not chosen:
            return  # cancelled
        self.shared_dir_changed.emit(chosen)
        # The App's slot updates the pref + re-installs the provider
        # synchronously, so by the time the signal returns the
        # storage layer sees the new value. Refresh the UI now.
        self._sync_shared_path_field()
        self._refresh_template_combo()
        self._update_toolbar_state()

    def _on_shared_clear(self) -> None:
        """Disconnect the editor from the shared library. Local user
        templates stay; only the team library disappears from the
        combo. The pref is wiped too so the next launch starts
        clean."""
        self.shared_dir_changed.emit("")
        self._sync_shared_path_field()
        self._refresh_template_combo()
        self._update_toolbar_state()

    # ---------------- Save / Delete / Apply ------------------------

    def _on_save(self) -> None:
        slug = self._current_slug
        if not slug:
            self._on_save_as()
            return
        # A template loaded from the shared library round-trips back
        # to the shared library on Save — the user clearly wants to
        # update the team copy, not silently fork a local override.
        # A user-template Save goes back to the local user dir.
        # Builtins fall through to Save As (the slug isn't owned).
        if is_user_template(slug):
            save_user_template(slug, self._template)
            self._dirty = False
            return
        if is_shared_template(slug):
            try:
                save_shared_template(slug, self._template)
            except RuntimeError as exc:
                QMessageBox.warning(
                    self, "Shared save failed",
                    f"Couldn't save to the shared library:\n\n{exc}",
                )
                return
            self._dirty = False
            return
        # Pure builtin — no slug we own to save to, escalate to
        # Save As so the user picks a name + location.
        self._on_save_as()

    def _on_save_as(self) -> None:
        name, ok = QInputDialog.getText(
            self, "Save burnin template as…",
            "Template name:",
            QLineEdit.EchoMode.Normal,
            self._template.name or "",
        )
        if not ok:
            return
        slug = slugify(name)
        # Update the template's display name too — saves the user
        # from having to edit the JSON later.
        new_template = replace(self._template, name=name or slug)

        # Save location: ask the user when a shared library is
        # configured. The default points at "Shared" so a team
        # admin's typical workflow ("Open editor → Save As → name
        # it → Save") publishes straight to the team without
        # an extra click. Locals can override by picking "Local
        # (this machine)".
        target_is_shared = False
        if shared_burnins_dir() is not None:
            # Use a custom button-set so the labels read clearly
            # ("Shared" / "Local" instead of "Yes" / "No" — Save
            # destination isn't a yes/no question).
            box = QMessageBox(self)
            box.setWindowTitle("Save where?")
            box.setText(
                f"Save '{slug}' to the team's shared library, "
                "or keep it local to this machine?",
            )
            shared_btn = box.addButton(
                "Shared (team)",
                QMessageBox.ButtonRole.AcceptRole,
            )
            local_btn = box.addButton(
                "Local (this machine)",
                QMessageBox.ButtonRole.AcceptRole,
            )
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(shared_btn)
            box.exec()
            clicked = box.clickedButton()
            if clicked is None or clicked not in (shared_btn, local_btn):
                return  # cancelled / X-closed
            target_is_shared = (clicked is shared_btn)

        # Existence check applies to the chosen target.
        if target_is_shared:
            existing = is_shared_template(slug)
        else:
            existing = is_user_template(slug)
        if existing:
            where = "shared library" if target_is_shared else "local"
            ret = QMessageBox.question(
                self, "Overwrite template",
                f"Template '{slug}' already exists in the {where}. "
                "Overwrite?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return

        try:
            if target_is_shared:
                save_shared_template(slug, new_template)
            else:
                save_user_template(slug, new_template)
        except RuntimeError as exc:
            QMessageBox.warning(
                self, "Save failed",
                f"Couldn't save the template:\n\n{exc}",
            )
            return
        self._template = new_template
        self._current_slug = slug
        self._dirty = False
        self._refresh_template_combo()
        # Snap the combo to the just-saved slug.
        idx = self._template_combo.findData(slug)
        if idx >= 0:
            self._template_combo.setCurrentIndex(idx)
        self._update_toolbar_state()

    def _on_delete(self) -> None:
        slug = self._current_slug
        if not slug:
            return
        # Two deletable kinds: local user template, or shared
        # template. Builtins can't be deleted (the slug just falls
        # back to the shipped version anyway).
        is_local = is_user_template(slug)
        is_shared = is_shared_template(slug)
        if not (is_local or is_shared):
            return
        where = "user (local)" if is_local else "shared (team)"
        ret = QMessageBox.question(
            self, "Delete template",
            f"Delete the {where} template '{slug}'? "
            f"This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        ok = (
            delete_user_template(slug) if is_local
            else delete_shared_template(slug)
        )
        if not ok and is_shared:
            QMessageBox.warning(
                self, "Delete failed",
                "Couldn't remove the file — the shared folder may be "
                "read-only on this machine or the file is open.",
            )
            return
        # Re-resolve the slug — it may fall back to a builtin if the
        # user template shadowed one; otherwise pick the first slug.
        self._refresh_template_combo()
        if self._template_combo.count() > 0:
            self._template_combo.setCurrentIndex(0)

    def _on_apply(self) -> None:
        if not self._current_slug:
            QMessageBox.information(
                self, "Set as active",
                "Save the template first so it has a slug to set active.",
            )
            return
        self.template_applied.emit(self._current_slug)

    # ---------------- Public ---------------------------------------

    def set_current_slug(self, slug: str) -> None:
        """Programmatic slug selection — used by the App when opening
        the editor on the currently-active template."""
        if slug == self._current_slug:
            return
        idx = self._template_combo.findData(slug)
        if idx >= 0:
            self._template_combo.setCurrentIndex(idx)
