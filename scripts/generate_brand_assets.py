#!/usr/bin/env python3
"""Generate the brand images the integration ships in its own brand/ folder.

Since Home Assistant 2026.3 a custom integration provides its own icons from
``custom_components/<domain>/brand/``, and Home Assistant no longer accepts
custom integration icons into the home-assistant/brands repository. This writes
that folder directly.

Produces trimmed PNGs at the sizes Home Assistant expects:

    icon.png      256x256
    icon@2x.png   512x512
    logo.png      shortest side between 128 and 256
    logo@2x.png   shortest side between 256 and 512

The mark is a shield (compliance) carrying a 3x3 grid of host dots. Six dots are
Fleet green; the three lower-right dots — the positions Fleet's own dot-grid
logo leaves empty — are white.

It does not use Home Assistant branding, which would wrongly imply this is an
official integration.

Usage:
    python scripts/generate_brand_assets.py

Requires Pillow, which is not a runtime dependency of the integration:
    pip install Pillow
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Supersample, then downscale, so edges come out smooth without any explicit
# antialiasing work.
SUPERSAMPLE = 4

WHITE = (255, 255, 255, 255)

# Sampled from Fleet's own logo (fleetdm.com), whose mark is a 3x3 dot grid with
# the lower-right diagonal left empty.
FLEET_NAVY = (25, 33, 71, 255)  # #192147, the Fleet wordmark colour
FLEET_GREEN = (99, 199, 64, 255)  # #63C740, the first dot of Fleet's grid
SHIELD_TOP = (38, 53, 107, 255)  # A lift of the navy, for a subtle gradient

# Row-major. True is a Fleet green dot, False is white. The white cells are the
# three positions Fleet's own mark leaves empty.
FLEET_DOT_GRID = (
    (True, True, True),
    (True, True, False),
    (True, False, False),
)

MACOS_FONTS = [
    "/System/Library/Fonts/SFNSDisplay.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def _quadratic(p0, p1, p2, steps: int = 60):
    """Sample a quadratic bezier curve."""
    points = []
    for i in range(steps + 1):
        t = i / steps
        inv = 1 - t
        x = inv * inv * p0[0] + 2 * inv * t * p1[0] + t * t * p2[0]
        y = inv * inv * p0[1] + 2 * inv * t * p1[1] + t * t * p2[1]
        points.append((x, y))
    return points


def shield_outline(size: int) -> list[tuple[float, float]]:
    """Build a classic shield silhouette that fills the canvas."""
    w = h = size
    shoulder = 0.42 * h

    points: list[tuple[float, float]] = [(0.0, 0.0), (0.0, shoulder)]
    # Left shoulder sweeping down to the bottom point.
    points += _quadratic((0.0, shoulder), (0.06 * w, 0.84 * h), (0.5 * w, h))
    # Back up the right side, mirrored.
    points += _quadratic((0.5 * w, h), (0.94 * w, 0.84 * h), (w, shoulder))
    points += [(w, 0.0)]
    return points


def _vertical_gradient(size: int, top: tuple, bottom: tuple) -> Image.Image:
    """Render a vertical gradient the size of the canvas."""
    gradient = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        gradient.putpixel(
            (0, y),
            tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(4)),
        )
    return gradient.resize((size, size))


def render_icon(size: int) -> Image.Image:
    """Render the square icon at the requested size."""
    canvas = size * SUPERSAMPLE

    # Shield silhouette as a mask, filled with a gradient.
    mask = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(mask).polygon(shield_outline(canvas), fill=255)

    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    image.paste(_vertical_gradient(canvas, SHIELD_TOP, FLEET_NAVY), (0, 0), mask)

    draw = ImageDraw.Draw(image)

    # A full 3x3 grid of host dots. The geometry is chosen so that even the
    # outer dots of the bottom row sit well inside the shield's taper.
    radius = 0.070 * canvas
    origin_x, origin_y = 0.265 * canvas, 0.215 * canvas
    step = 0.235 * canvas
    for row, filled in enumerate(FLEET_DOT_GRID):
        for col, is_green in enumerate(filled):
            cx = origin_x + col * step
            cy = origin_y + row * step
            draw.ellipse(
                (cx - radius, cy - radius, cx + radius, cy + radius),
                fill=FLEET_GREEN if is_green else WHITE,
            )

    return image.resize((size, size), Image.LANCZOS)


def _load_font(size: int) -> ImageFont.FreeTypeFont | None:
    """Load a bold system font, or return None if none is available."""
    for path in MACOS_FONTS:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return None


def render_logo(height: int) -> Image.Image | None:
    """Render a horizontal logo: the icon plus a wordmark.

    Returns None when no usable system font is found, since an unreadable
    wordmark is worse than shipping only an icon.
    """
    font = _load_font(int(height * 0.62))
    if font is None:
        return None

    icon = render_icon(height)
    gap = int(height * 0.18)

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    left, top, right, bottom = probe.textbbox((0, 0), "Fleet", font=font)
    text_w, text_h = right - left, bottom - top

    image = Image.new("RGBA", (height + gap + text_w, height), (0, 0, 0, 0))
    image.paste(icon, (0, 0), icon)
    ImageDraw.Draw(image).text(
        (height + gap - left, (height - text_h) / 2 - top),
        "Fleet",
        font=font,
        fill=FLEET_NAVY,
    )
    # Brands requires minimal empty space at the edges, and font metrics leave a
    # few pixels of side bearing after the final glyph.
    if (bbox := image.getbbox()) is not None:
        image = image.crop((0, 0, bbox[2], height))
    return image


def main() -> None:
    """Write the brand assets to disk."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("custom_components/fleetdm/brand"),
        help=(
            "Output directory. This is the brand folder Home Assistant 2026.3+ "
            "reads, and the path the HACS validation action checks "
            "(default: %(default)s)"
        ),
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
        path = args.out / name
        render_icon(size).save(path, "PNG", optimize=True)
        print(f"wrote {path} ({size}x{size})")

    for name, height in (("logo.png", 256), ("logo@2x.png", 512)):
        logo = render_logo(height)
        if logo is None:
            print("skipped logo: no usable system font found")
            break
        path = args.out / name
        logo.save(path, "PNG", optimize=True)
        print(f"wrote {path} ({logo.width}x{logo.height})")


if __name__ == "__main__":
    main()
