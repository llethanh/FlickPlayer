"""Generate the Flick Player mark as a PNG (and the multi-size ICO).

Run from the repo root with the ``img_player`` conda env activated:
    python docs/branding/generate_icon.py

Outputs ``docs/branding/flick_icon_<size>.png`` for visual review and
``src/img_player/assets/icons/flick.ico`` for the PyInstaller build.

Design philosophy: see ``docs/branding/PHILOSOPHY.md``. Mark is a
deep-neutral rounded square, a single warm-orange offset play
triangle, and four micro-perforations along the edges suggesting a
35mm film strip. Two colours total. Hinted to read at 16 / 32 / 48 /
256 px without losing identity.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

# --- Tokens -------------------------------------------------------------

# Two-value palette. Background reads as "the dark UI", the accent is
# the same orange the app uses elsewhere (theme.H.ACCENT).
GROUND = "#141416"   # near-black, matches BG_DEEP
ACCENT = "#E8901C"   # the warm hearth-ember accent

# Master-size canvas. We render once at 1024 px and downscale with
# Lanczos for the smaller variants. Avoids re-tuning pixel hints at
# every size — the master geometry is what carries identity.
MASTER = 1024


def _rounded_square(size: int, radius_pct: float, fill: str) -> Image.Image:
    """Pure rounded-square, transparent outside the corner radius."""
    radius = int(size * radius_pct)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=radius, fill=fill,
    )
    return img


def _flick_f(size: int, fill: str) -> Image.Image:
    """Bold geometric ``F`` mark, drawn as three rectangles so it
    hints cleanly to a pixel grid at every size. No type face, no
    serifs — pure sans geometry the way a stencil would cut it.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Inset from the square so the strokes don't kiss the canvas edge.
    pad = int(size * 0.06)
    left = pad
    right = size - pad
    top = pad
    bottom = size - pad
    bbox_w = right - left
    bbox_h = bottom - top

    # Geometry. Tuned by eye until each stroke reads at 16 px.
    stem_w = int(bbox_w * 0.30)         # vertical stroke thickness
    bar_h = int(bbox_h * 0.22)          # top / mid bar thickness
    top_bar_w = bbox_w                  # top bar spans full width
    mid_bar_w = int(bbox_w * 0.68)      # mid bar shorter (refined F)
    mid_bar_y = top + int(bbox_h * 0.40)

    # Vertical stem — full height.
    draw.rectangle(
        [left, top, left + stem_w, bottom],
        fill=fill,
    )
    # Top arm — full width, sits flush on top of the stem.
    draw.rectangle(
        [left, top, left + top_bar_w, top + bar_h],
        fill=fill,
    )
    # Middle arm — shorter, slightly above optical centre so the
    # negative space below feels balanced (a centred middle bar
    # always reads "too high" because the eye reads the top bar plus
    # stem as one mass).
    draw.rectangle(
        [left, mid_bar_y, left + mid_bar_w, mid_bar_y + bar_h],
        fill=fill,
    )
    return img


def _film_perforations(
    size: int, fill: str, count: int = 4, *, opacity: int = 60,
) -> Image.Image:
    """Four micro-notches on each vertical edge — the film-strip nod.

    Tiny, low-opacity: present to anyone who looks for them, invisible
    to anyone who doesn't. The marks are the same warm accent as the
    play triangle but painted at ~24% alpha so they read as texture,
    not signal.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    perf_w = int(size * 0.045)         # width of each perforation
    perf_h = int(size * 0.10)          # height of each perforation
    radius = int(perf_w * 0.40)        # gentle rounding
    edge_inset = int(size * 0.055)     # how far in from the canvas edge
    # Distribute the perforations vertically with a gap between them
    # equal to one perforation height — gives a steady rhythm without
    # crowding.
    total_h = count * perf_h + (count - 1) * perf_h
    start_y = (size - total_h) // 2
    rgba = _hex_to_rgba(fill, opacity)
    for i in range(count):
        cy = start_y + i * (perf_h * 2)
        # Left edge
        x0 = edge_inset
        draw.rounded_rectangle(
            (x0, cy, x0 + perf_w, cy + perf_h),
            radius=radius,
            fill=rgba,
        )
        # Right edge — mirrored
        x0 = size - edge_inset - perf_w
        draw.rounded_rectangle(
            (x0, cy, x0 + perf_w, cy + perf_h),
            radius=radius,
            fill=rgba,
        )
    return img


def _hex_to_rgba(hex_color: str, alpha: int) -> tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def render_master(size: int = MASTER) -> Image.Image:
    """Compose the mark at master resolution.

    Layer order (back to front):
      1. Rounded-square ground (dark)
      2. Film perforations on the edges (warm, faint)
      3. Single play triangle (warm, full strength), offset slightly
         left so it leans into its motion rather than sitting dead
         centre.
    """
    canvas = _rounded_square(size, radius_pct=0.18, fill=GROUND)

    perfs = _film_perforations(size, fill=ACCENT, count=4, opacity=55)
    canvas = Image.alpha_composite(canvas, perfs)

    f_size = int(size * 0.52)
    mark = _flick_f(f_size, fill=ACCENT)
    # Optical centring for the F: its mass sits on the LEFT (stem +
    # both bars) so the bounding-box centre reads as "right-leaning"
    # when placed at canvas centre. Pull it left a touch so the eye
    # accepts it as centred. The film-perforation rhythm on the
    # outside frame anchors the mark's vertical placement.
    offset_x = -int(size * 0.02)
    px = (size - f_size) // 2 + offset_x
    py = (size - f_size) // 2
    canvas.alpha_composite(mark, dest=(px, py))
    return canvas


def emit_pngs(master: Image.Image, out_dir: Path) -> list[Path]:
    """Save the master + a few representative sizes for visual review.

    Production sizes for the .ico are emitted by ``emit_ico`` directly
    from the master image, so this function is purely for "show the
    user what they're getting" purposes.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for px in (1024, 256, 64, 32, 16):
        if px == master.size[0]:
            img = master
        else:
            img = master.resize((px, px), Image.Resampling.LANCZOS)
        p = out_dir / f"flick_icon_{px}.png"
        img.save(p, format="PNG")
        paths.append(p)
    return paths


def emit_ico(master: Image.Image, out_path: Path) -> Path:
    """Multi-resolution Windows ICO. PIL auto-builds the .ico
    container from a single image when given a list of sizes."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
             (128, 128), (256, 256)]
    master.save(out_path, format="ICO", sizes=sizes)
    return out_path


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent.parent
    branding = repo_root / "docs" / "branding"
    icon_dest = repo_root / "src" / "img_player" / "assets" / "icons" / "flick.ico"

    master = render_master()
    pngs = emit_pngs(master, branding)
    ico = emit_ico(master, icon_dest)

    print("[flick-icon] PNG preview sizes written:")
    for p in pngs:
        print(f"  {p.relative_to(repo_root)}")
    print(f"[flick-icon] ICO bundle written: {ico.relative_to(repo_root)}")


if __name__ == "__main__":
    main()
