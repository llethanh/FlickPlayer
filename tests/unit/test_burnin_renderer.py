"""Pixel-level tests for the burnin renderer.

Pillow + numpy let us run these headlessly (no Qt fixture needed).
We pin:

* Shape preservation — render never changes (H, W, 4).
* Disabled bars → image unchanged.
* Enabled bar → the band region differs from input; the non-band
  region is identical.
* Element rendering — text leaves dark/coloured pixels in the bar.
* Color parsing edge cases (hex, rgba, malformed).
* MIN_BAR_PX clamping for tiny images.

The text fidelity isn't pinned (font rendering varies by OS / Pillow
version) — we only check "*something* was painted in this region".
"""

from __future__ import annotations

import numpy as np
import pytest

from img_player.burnins.builtins import builtin_template
from img_player.burnins.model import (
    BurninBar,
    BurninTemplate,
    ImageElement,
    SpacerElement,
    TextElement,
)
from img_player.burnins.renderer import (
    _parse_color,
    render_burnin,
)
from img_player.burnins.tokens import RenderContext


# ---------------------------------------------------------------------- Helpers

def _grey_image(h: int = 200, w: int = 400, level: int = 80) -> np.ndarray:
    """Solid grey RGBA image used as the background under burnins.

    Grey rather than zero so a test can assert "this pixel is not the
    original background" without false negatives from a fully-black
    image where every drawn pixel might happen to land on 0.
    """
    img = np.full((h, w, 4), (level, level, level, 255), dtype=np.uint8)
    return img


def _ctx() -> RenderContext:
    return RenderContext(
        frame=1042,
        frame_total=1244,
        fps=24.0,
        width=1920,
        height=1080,
        sequence="SH0010_Rendered.####.exr",
        layer_name="plate",
        date="2026-05-27",
        user="reviewer",
    )


# ---------------------------------------------------------------------- Shape & no-op paths

class TestShapePreservation:
    def test_shape_unchanged_with_both_bars(self) -> None:
        img = _grey_image()
        out = render_burnin(img, builtin_template("default"), _ctx())
        assert out.shape == img.shape
        assert out.dtype == np.uint8

    def test_shape_unchanged_with_no_bars(self) -> None:
        img = _grey_image()
        tpl = BurninTemplate(
            top_bar=BurninBar(enabled=False),
            bottom_bar=BurninBar(enabled=False),
        )
        out = render_burnin(img, tpl, _ctx())
        assert out.shape == img.shape

    def test_disabled_bars_leave_image_pixels_intact(self) -> None:
        # When both bars are off, the renderer returns a fresh copy
        # of the input with no modifications.
        img = _grey_image()
        tpl = BurninTemplate(
            top_bar=BurninBar(enabled=False),
            bottom_bar=BurninBar(enabled=False),
        )
        out = render_burnin(img, tpl, _ctx())
        assert np.array_equal(out, img)

    def test_non_rgba_input_returned_as_copy(self) -> None:
        # Defensive: an RGB (3-channel) array gets a copy returned
        # unmodified rather than crashing in PIL conversion.
        img = np.full((100, 100, 3), 128, dtype=np.uint8)
        out = render_burnin(img, builtin_template("default"), _ctx())
        assert np.array_equal(out, img)

    def test_zero_size_image_returned_as_copy(self) -> None:
        img = np.zeros((0, 0, 4), dtype=np.uint8)
        out = render_burnin(img, builtin_template("default"), _ctx())
        assert out.shape == img.shape


# ---------------------------------------------------------------------- Region pinning

class TestBarRegions:
    def test_top_bar_region_differs_bottom_region_intact(self) -> None:
        # When only the top bar is enabled, only the top region pixels
        # change. The bottom region matches the input.
        img = _grey_image(h=200, w=400)
        tpl = BurninTemplate(
            top_bar=BurninBar(
                enabled=True, height_pct=0.10,
                bg_color="rgba(0, 0, 0, 1.0)",  # full-opaque so pixels DEFINITELY change
            ),
            bottom_bar=BurninBar(enabled=False),
        )
        out = render_burnin(img, tpl, _ctx())
        # Top band (10 % = 20 px → clamped to MIN_BAR_PX max(16, 20) = 20).
        assert not np.array_equal(out[:20, :, :], img[:20, :, :]), (
            "top bar region was not modified"
        )
        # Bottom half untouched.
        assert np.array_equal(out[50:, :, :], img[50:, :, :])

    def test_bottom_bar_region_differs_top_region_intact(self) -> None:
        img = _grey_image(h=200, w=400)
        tpl = BurninTemplate(
            top_bar=BurninBar(enabled=False),
            bottom_bar=BurninBar(
                enabled=True, height_pct=0.10,
                bg_color="rgba(0, 0, 0, 1.0)",
            ),
        )
        out = render_burnin(img, tpl, _ctx())
        # Bottom band: last 20 px (10 % of 200).
        assert not np.array_equal(out[-20:, :, :], img[-20:, :, :])
        # Top half untouched.
        assert np.array_equal(out[:100, :, :], img[:100, :, :])

    def test_min_bar_px_floor_applies(self) -> None:
        # On a tiny image, height_pct would give < MIN_BAR_PX. We
        # clamp upward so the bar stays legible — checked by
        # asserting the painted band is at least MIN_BAR_PX tall.
        img = _grey_image(h=50, w=200)
        tpl = BurninTemplate(
            top_bar=BurninBar(
                enabled=True, height_pct=0.01,   # 0.01 × 50 = 0.5 px
                bg_color="rgba(0, 0, 0, 1.0)",
            ),
            bottom_bar=BurninBar(enabled=False),
        )
        out = render_burnin(img, tpl, _ctx())
        # The first 16 rows should be modified (MIN_BAR_PX = 16).
        assert not np.array_equal(out[:16, :, :], img[:16, :, :])

    def test_bars_too_tall_for_image_renders_unchanged(self) -> None:
        # Image too short for both bars + at least one row of image —
        # safer to skip the burnin than to overlap bars.
        img = _grey_image(h=30, w=200)
        tpl = BurninTemplate(
            top_bar=BurninBar(enabled=True, height_pct=0.6),    # 18 px
            bottom_bar=BurninBar(enabled=True, height_pct=0.6),  # another 18 px → > 30
        )
        out = render_burnin(img, tpl, _ctx())
        assert np.array_equal(out, img)


# ---------------------------------------------------------------------- Element drawing

class TestTextDrawing:
    def test_left_anchored_text_modifies_left_side(self) -> None:
        img = _grey_image(h=200, w=600)
        tpl = BurninTemplate(
            top_bar=BurninBar(
                enabled=True, height_pct=0.20,    # 40 px
                bg_color="rgba(0, 0, 0, 0.0)",    # transparent bg — text must paint itself
                elements=(
                    TextElement(
                        anchor="left",
                        offset_x=8,
                        text="LEFT",
                        font_family="Arial",
                        font_size_pt=18,
                        color="#FFFFFF",
                    ),
                ),
            ),
            bottom_bar=BurninBar(enabled=False),
        )
        out = render_burnin(img, tpl, _ctx())
        # Pixels in the left half of the bar region should differ
        # from the input (text painted there); the far right should
        # be unchanged.
        left_changed = not np.array_equal(out[:40, :100, :], img[:40, :100, :])
        far_right_unchanged = np.array_equal(out[:40, 500:, :], img[:40, 500:, :])
        assert left_changed
        assert far_right_unchanged

    def test_right_anchored_text_modifies_right_side(self) -> None:
        img = _grey_image(h=200, w=600)
        tpl = BurninTemplate(
            top_bar=BurninBar(
                enabled=True, height_pct=0.20,
                bg_color="rgba(0, 0, 0, 0.0)",
                elements=(
                    TextElement(
                        anchor="right",
                        offset_x=-8,
                        text="RIGHT",
                        font_family="Arial",
                        font_size_pt=18,
                        color="#FFFFFF",
                    ),
                ),
            ),
            bottom_bar=BurninBar(enabled=False),
        )
        out = render_burnin(img, tpl, _ctx())
        # Right portion of the bar region should differ.
        right_changed = not np.array_equal(out[:40, 500:, :], img[:40, 500:, :])
        far_left_unchanged = np.array_equal(out[:40, :100, :], img[:40, :100, :])
        assert right_changed
        assert far_left_unchanged

    def test_center_anchored_text_modifies_centre(self) -> None:
        img = _grey_image(h=200, w=600)
        tpl = BurninTemplate(
            top_bar=BurninBar(
                enabled=True, height_pct=0.20,
                bg_color="rgba(0, 0, 0, 0.0)",
                elements=(
                    TextElement(
                        anchor="center",
                        text="MIDDLE",
                        font_family="Arial",
                        font_size_pt=18,
                        color="#FFFFFF",
                    ),
                ),
            ),
            bottom_bar=BurninBar(enabled=False),
        )
        out = render_burnin(img, tpl, _ctx())
        # Centre region of the bar should be modified.
        centre_changed = not np.array_equal(
            out[:40, 250:350, :], img[:40, 250:350, :],
        )
        far_left_unchanged = np.array_equal(out[:40, :50, :], img[:40, :50, :])
        far_right_unchanged = np.array_equal(out[:40, 550:, :], img[:40, :50, :])
        assert centre_changed
        assert far_left_unchanged
        assert far_right_unchanged


class TestImageElementMissingPath:
    def test_missing_image_does_not_break_other_elements(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        # When the image path can't be opened we silently skip it
        # and continue drawing the rest of the bar.
        img = _grey_image(h=200, w=400)
        tpl = BurninTemplate(
            top_bar=BurninBar(
                enabled=True, height_pct=0.20,
                bg_color="rgba(0, 0, 0, 1.0)",
                elements=(
                    ImageElement(
                        anchor="left",
                        path=str(tmp_path / "nonexistent.png"),
                        height_pct=0.8,
                    ),
                    TextElement(
                        anchor="right",
                        offset_x=-8,
                        text="STILL HERE",
                        font_family="Arial",
                        font_size_pt=18,
                        color="#FFFFFF",
                    ),
                ),
            ),
            bottom_bar=BurninBar(enabled=False),
        )
        # The point of the test is that this DOESN'T raise.
        out = render_burnin(img, tpl, _ctx())
        # And the bar itself was still drawn.
        assert not np.array_equal(out[:40, :, :], img[:40, :, :])


class TestBackgroundFill:
    def test_opaque_bg_paints_uniform_band(self) -> None:
        # With an opaque coloured background and no elements, every
        # pixel of the bar should be the bg colour.
        img = _grey_image(h=200, w=400)
        tpl = BurninTemplate(
            top_bar=BurninBar(
                enabled=True, height_pct=0.10,
                bg_color="#FF0000",   # opaque red
                elements=(),
            ),
            bottom_bar=BurninBar(enabled=False),
        )
        out = render_burnin(img, tpl, _ctx())
        band = out[:20, :, :]
        # Every pixel red. PIL writes RGBA; alpha stays 255.
        assert (band[..., 0] == 255).all()
        assert (band[..., 1] == 0).all()
        assert (band[..., 2] == 0).all()

    def test_translucent_bg_blends_with_image(self) -> None:
        # 50 % black on a grey-80 image → grey ≈ 40.
        img = _grey_image(h=200, w=400, level=80)
        tpl = BurninTemplate(
            top_bar=BurninBar(
                enabled=True, height_pct=0.10,
                bg_color="rgba(0, 0, 0, 0.5)",
                elements=(),
            ),
            bottom_bar=BurninBar(enabled=False),
        )
        out = render_burnin(img, tpl, _ctx())
        band = out[:20, :, :]
        # Allow ±2 for Pillow's rounding.
        avg_r = int(band[..., 0].mean())
        assert 38 <= avg_r <= 42


# ---------------------------------------------------------------------- Color parsing

class TestParseColor:
    def test_rgb_hex(self) -> None:
        assert _parse_color("#FF8000") == (255, 128, 0, 255)

    def test_rgba_hex(self) -> None:
        assert _parse_color("#FF8000C0") == (255, 128, 0, 0xC0)

    def test_rgba_function_float_alpha(self) -> None:
        assert _parse_color("rgba(20, 20, 22, 0.5)") == (20, 20, 22, 128)

    def test_rgba_function_integer_alpha(self) -> None:
        assert _parse_color("rgba(20, 20, 22, 128)") == (20, 20, 22, 128)

    def test_rgb_function_no_alpha_means_opaque(self) -> None:
        assert _parse_color("rgb(10, 20, 30)") == (10, 20, 30, 255)

    @pytest.mark.parametrize("bad", [
        "", "not a color", "#XYZ", "rgba(foo, bar, baz, 1)",
    ])
    def test_malformed_falls_back_white(self, bad: str) -> None:
        # Fallback rather than raising — burnin keeps rendering with a
        # visible white element so the typo is obvious.
        assert _parse_color(bad) == (255, 255, 255, 255)


# ---------------------------------------------------------------------- Builtins integrate

class TestBuiltinsRender:
    @pytest.mark.parametrize("slug", ["default", "minimal", "studio_banner"])
    def test_each_builtin_renders_without_error(self, slug: str) -> None:
        img = _grey_image()
        out = render_burnin(img, builtin_template(slug), _ctx())
        # Sanity: the renderer didn't crash and returned the same shape.
        assert out.shape == img.shape


class TestElementRects:
    """The renderer optionally populates ``out_element_rects`` with the
    bounding box of each drawn element, keyed by
    ``(bar_id, original_element_index)``. The editor uses this for the
    "click on preview → select in tree" hit-test."""

    def test_no_dict_means_no_record(self) -> None:
        # Default call (no kwarg) returns just the pixels — no
        # observable side-effect.
        img = _grey_image()
        render_burnin(img, builtin_template("default"), _ctx())
        # Nothing to assert beyond "no crash" — the kwarg defaults to
        # None, so there's no shared state to clobber.

    def test_populates_keys_for_each_element(self) -> None:
        img = _grey_image(h=300, w=600)
        rects: dict = {}
        # ``minimal`` ships with one bottom-bar element (a {frame}
        # counter centred).
        render_burnin(
            img, builtin_template("minimal"), _ctx(),
            out_element_rects=rects,
        )
        assert ("bottom", 0) in rects

    def test_dailies_default_records_both_bars(self) -> None:
        img = _grey_image(h=300, w=600)
        rects: dict = {}
        render_burnin(
            img, builtin_template("default"), _ctx(),
            out_element_rects=rects,
        )
        # Dailies has 2 elements on each bar.
        assert ("top", 0) in rects
        assert ("top", 1) in rects
        assert ("bottom", 0) in rects
        assert ("bottom", 1) in rects

    def test_recorded_rect_lies_inside_image(self) -> None:
        # Each rect should be a 4-tuple of ints within the image's
        # bounds — the editor relies on this for hit-test sanity.
        img = _grey_image(h=300, w=600)
        rects: dict = {}
        render_burnin(
            img, builtin_template("default"), _ctx(),
            out_element_rects=rects,
        )
        for (bar_id, idx), (x, y, w, h) in rects.items():
            assert bar_id in ("top", "bottom")
            assert isinstance(idx, int) and idx >= 0
            assert isinstance(x, int)
            assert isinstance(y, int)
            assert w >= 0
            assert h >= 0
            # Roughly inside the image — allow a few px slop because
            # text bounding boxes can poke a pixel outside on glyph
            # ascender / descender.
            assert -10 <= x <= 600
            assert -10 <= y <= 300

    def test_disabled_bar_records_nothing(self) -> None:
        # If a bar is disabled, no element under it should record.
        # ``minimal`` has only bottom_bar enabled.
        img = _grey_image()
        rects: dict = {}
        render_burnin(
            img, builtin_template("minimal"), _ctx(),
            out_element_rects=rects,
        )
        for (bar_id, _idx) in rects:
            assert bar_id == "bottom"


class TestSizeDecoupledFromBar:
    """Element size scales with image height, NOT bar height. Doubling
    the bar's ``height_pct`` makes the bar twice as tall but leaves
    text + spacers exactly the same pixel size (the user explicitly
    asked for this — bar size and element size should be independent
    sliders, not linked)."""

    def _text_rect_height(
        self, image_h: int, height_pct: float,
    ) -> int:
        """Render a one-text-element template at the given bar height
        and return the recorded text height in pixels."""
        from img_player.burnins.model import (
            BurninBar, BurninTemplate, TextElement,
        )
        tpl = BurninTemplate(
            top_bar=BurninBar(
                enabled=True, height_pct=height_pct,
                bg_color="rgba(0, 0, 0, 0)",
                elements=(
                    TextElement(
                        anchor="left", offset_x=8,
                        text="HELLO",
                        font_family="Arial",
                        font_size_pt=24,
                        color="#FFFFFF",
                    ),
                ),
            ),
        )
        img = _grey_image(h=image_h, w=600)
        rects: dict = {}
        render_burnin(img, tpl, _ctx(), out_element_rects=rects)
        _, _, _, h = rects[("top", 0)]
        return int(h)

    def test_bar_height_does_not_affect_text_size(self) -> None:
        # Same image height, bar at 6 % vs 12 % → text height should
        # be identical (modulo Pillow rounding).
        small_bar = self._text_rect_height(image_h=1080, height_pct=0.06)
        big_bar = self._text_rect_height(image_h=1080, height_pct=0.12)
        assert abs(small_bar - big_bar) <= 1

    def test_image_height_still_scales_text(self) -> None:
        # Resolution scaling stays — same template at 1080 vs 540
        # produces proportional text sizes (roughly 2× ratio).
        at_1080 = self._text_rect_height(image_h=1080, height_pct=0.06)
        at_540 = self._text_rect_height(image_h=540, height_pct=0.06)
        # Ratio approximately 2 (allow some slack for Pillow rounding).
        assert at_1080 >= at_540
        assert at_540 > 0
        ratio = at_1080 / at_540
        assert 1.5 <= ratio <= 2.5


class TestTextOpacity:
    """The text colour's alpha channel must be **alpha-composited**
    onto the bar background — not punched through it.

    PIL's ``ImageDraw.text`` with an RGBA ``fill`` writes pixels
    directly: ``fill=(R, G, B, 0)`` would overwrite bar-bg pixels
    with fully transparent ones, leaving text-shaped HOLES in the
    bar (the user reported this as "the typography becomes dark
    when opacity is set to 0%" because the holes exposed the GL
    black background underneath).
    The renderer must instead draw text onto a transparent layer
    and ``alpha_composite`` it, so alpha=0 → no visible change and
    alpha=128 → text blends into the bar bg.
    """

    def _solid_bar_template(self, text_color: str) -> BurninTemplate:
        # A fully opaque bar background so any "punch through" caused
        # by a transparent text fill would be obvious as a colour shift.
        return BurninTemplate(
            top_bar=BurninBar(
                enabled=True,
                height_pct=0.10,
                bg_color="rgba(40, 40, 40, 1.0)",
                elements=(
                    TextElement(
                        anchor="left", offset_x=20,
                        text="OPACITY",
                        font_family="Arial",
                        font_size_pt=48,
                        color=text_color,
                    ),
                ),
            ),
            bottom_bar=BurninBar(enabled=False),
        )

    def test_text_alpha_zero_leaves_bar_bg_intact(self) -> None:
        # Render with text fully transparent. The bar region must be
        # uniformly the bar's bg colour — no text-shaped holes.
        img = _grey_image(h=600, w=800, level=80)
        tpl = self._solid_bar_template("rgba(255, 255, 255, 0.00)")
        rects: dict = {}
        out = render_burnin(img, tpl, _ctx(), out_element_rects=rects)
        # Find the text rect.
        tx, ty, tw, th = rects[("top", 0)]
        # Sample the centre of the text box. With the bug, this pixel
        # would land on a transparent hole (alpha != 255, or alpha=255
        # composited against widget bg). With the fix, it must still
        # be the bar's bg colour: rgba(40, 40, 40, 1.0) composited
        # over grey 80 → solid 40 (because bar alpha is 1.0).
        cy = int(ty + th // 2)
        cx = int(tx + tw // 2)
        # The bar bg is fully opaque grey 40; the entire bar area
        # should be exactly that, regardless of whether the text pixel
        # at (cx, cy) sits on a glyph or between glyphs.
        # If the bug were still present, this pixel might be 80
        # (the original grey leaking through a hole) or some other
        # value — but NEVER 40 unaltered.
        r, g, b, a = out[cy, cx]
        # bar bg colour, fully opaque
        assert (int(r), int(g), int(b)) == (40, 40, 40)
        assert int(a) == 255

    def test_text_alpha_zero_does_not_change_bar_pixels(self) -> None:
        # Stronger version: render twice — once with NO text element,
        # once with the same template but text alpha=0. The bar
        # region must be pixel-for-pixel identical.
        img = _grey_image(h=600, w=800, level=80)
        tpl_no_text = BurninTemplate(
            top_bar=BurninBar(
                enabled=True, height_pct=0.10,
                bg_color="rgba(40, 40, 40, 1.0)",
                elements=(),
            ),
            bottom_bar=BurninBar(enabled=False),
        )
        out_no_text = render_burnin(img, tpl_no_text, _ctx())
        out_invisible_text = render_burnin(
            img, self._solid_bar_template("rgba(255, 255, 255, 0.00)"),
            _ctx(),
        )
        assert np.array_equal(out_no_text, out_invisible_text)

    def test_text_alpha_full_does_paint(self) -> None:
        # Sanity check: with alpha=1.0, the text SHOULD modify pixels
        # — the bar must contain at least some pixels that are NOT the
        # bar bg colour (i.e. the white text glyphs). Guards against a
        # regression where the new layer path silently swallows opaque
        # text too.
        img = _grey_image(h=600, w=800, level=80)
        tpl = self._solid_bar_template("rgba(255, 255, 255, 1.00)")
        rects: dict = {}
        out = render_burnin(img, tpl, _ctx(), out_element_rects=rects)
        tx, ty, tw, th = rects[("top", 0)]
        crop = out[int(ty):int(ty + th), int(tx):int(tx + tw)]
        # At least one pixel must be brighter than the bar bg (40).
        assert int(crop[..., 0].max()) > 100

    def test_descenders_not_clipped_at_bottom(self) -> None:
        # Regression for the "text cut off at the bottom" bug: when
        # the alpha-composite path sized the text layer to ``(w, h)``
        # but used Pillow's default ``"la"`` anchor (left-ASCENDER),
        # ``draw.text`` painted at y0..y1 inside the layer — and
        # descenders (g, p, y, q) landed at y > h, getting clipped.
        # The fix switches both ``textbbox`` and ``draw.text`` to
        # anchor ``"lt"`` (left-TOP-of-bbox), so painted pixels
        # span the full (0, 0)..(w, h) of the layer.
        img = _grey_image(h=600, w=800, level=80)
        tpl = self._solid_bar_template("rgba(255, 255, 255, 1.00)")
        # Replace the text with one full of descenders.
        bar = tpl.top_bar
        text_elem = bar.elements[0]
        from dataclasses import replace
        new_text = replace(text_elem, text="gpqy")
        tpl = replace(tpl, top_bar=replace(bar, elements=(new_text,)))
        rects: dict = {}
        out = render_burnin(img, tpl, _ctx(), out_element_rects=rects)
        tx, ty, tw, th = rects[("top", 0)]
        # Look at the BOTTOM strip of the text rect — descender pixels
        # should be there, brighter than the bar bg (40).
        bottom_strip = out[
            int(ty + th * 0.6):int(ty + th),
            int(tx):int(tx + tw),
        ]
        # At least one pixel in the bottom 40 % of the text rect must
        # be lit by the descender — i.e. brighter than the bar bg.
        assert int(bottom_strip[..., 0].max()) > 100, (
            "descender pixels were clipped — the text layer is sized "
            "to the bbox dims but Pillow paints past it when the "
            "anchor mismatch puts pixels below y=h"
        )

    def test_text_vertically_centred_in_bottom_bar(self) -> None:
        # Regression for "text in the bottom bar isn't centered".
        # Cause was the same anchor mismatch: ``"la"`` puts the
        # ascender line at the requested y, not the bbox top, so the
        # visible text was offset DOWN by ~y0 pixels relative to the
        # centring math. Most visible on the bottom bar (text closer
        # to bottom edge than top edge).
        # We pin: the distance from the topmost painted text pixel to
        # the top of the bar should approx. equal the distance from
        # the bottommost painted pixel to the bottom of the bar.
        img = _grey_image(h=600, w=800, level=80)
        tpl = BurninTemplate(
            top_bar=BurninBar(enabled=False),
            bottom_bar=BurninBar(
                enabled=True,
                height_pct=0.12,
                bg_color="rgba(40, 40, 40, 1.0)",
                elements=(
                    TextElement(
                        anchor="left", offset_x=20,
                        text="Mh",  # cap + ascender, no descender — symmetric vertically
                        font_family="Arial",
                        font_size_pt=48,
                        color="#FFFFFF",
                    ),
                ),
            ),
        )
        out = render_burnin(img, tpl, _ctx())
        # Bottom bar occupies the lower 12 % of the image.
        bar_top = int(round(600 * (1 - 0.12)))
        bar_bot = 600
        # In the bar, find rows that contain any white text pixel.
        bar_strip = out[bar_top:bar_bot, :, 0]  # red channel
        text_rows = np.where(bar_strip.max(axis=1) > 200)[0]
        assert text_rows.size > 0, "no text rendered in the bottom bar"
        top_padding = int(text_rows.min())
        bot_padding = int((bar_bot - bar_top - 1) - text_rows.max())
        # Allow a few pixels of asymmetry (fonts aren't perfectly
        # symmetric and the cap-height vs x-height differ), but the
        # bug was a ~y0-pixel offset (often 5-10 px at this size).
        # Pin to ≤ 6 px to catch the regression while tolerating
        # font-driver rounding.
        assert abs(top_padding - bot_padding) <= 6, (
            f"text not centred in bottom bar: top_pad={top_padding} "
            f"bot_pad={bot_padding}"
        )

    def test_text_alpha_half_blends_toward_bar_bg(self) -> None:
        # With alpha=0.5, glyph pixels should be a blend between text
        # colour (255) and bar bg (40) → roughly 147, definitely below
        # 200 (full opaque) and above 40 (no paint).
        img = _grey_image(h=600, w=800, level=80)
        tpl = self._solid_bar_template("rgba(255, 255, 255, 0.50)")
        rects: dict = {}
        out = render_burnin(img, tpl, _ctx(), out_element_rects=rects)
        tx, ty, tw, th = rects[("top", 0)]
        crop = out[int(ty):int(ty + th), int(tx):int(tx + tw)]
        peak = int(crop[..., 0].max())
        # Brighter than bar bg (paint happened) but dimmer than full
        # opaque white (alpha blended).
        assert 60 < peak < 220
