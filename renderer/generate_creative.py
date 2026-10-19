#!/usr/bin/env python3
"""Render an HDFC Festive Bonanza creative from the approved run template."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import secrets
import sys
from decimal import Decimal
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
except ImportError as exc:
    raise SystemExit("Pillow is required. Install it with: python3 -m pip install Pillow") from exc


SKILL_DIR = Path(__file__).resolve().parent.parent
LAYOUTS_PATH = SKILL_DIR / "references" / "template-layouts.json"
ASSETS_DIR = SKILL_DIR / "assets"
os.environ.setdefault("NUMBA_CACHE_DIR", "/tmp/festive-rembg-numba")
GRADIENT_PALETTES = {
    "royal-violet": ("#102A68", "#7B2CBF"),
    "teal-midnight": ("#0B6E75", "#081B3A"),
    "indigo-magenta": ("#2636A8", "#B11D76"),
    "emerald-blue": ("#087F5B", "#1948A8"),
    "sunset-plum": ("#F06A35", "#64206E"),
    "wine-violet": ("#7A1838", "#44238D"),
    "navy-gold": ("#071B3D", "#A56A12"),
    "cyan-indigo": ("#087EA4", "#302B8F"),
}
REGULAR_FONT_CANDIDATES = (
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    Path("/usr/share/fonts/opentype/urw-base35/NimbusSans-Regular.otf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
)
BOLD_FONT_CANDIDATES = (
    ASSETS_DIR / "fonts" / "Lato-Bold.ttf",
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/usr/share/fonts/opentype/urw-base35/NimbusSans-Bold.otf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
)
DISPLAY_FONT_CANDIDATES = (
    ASSETS_DIR / "fonts" / "LilitaOne-Regular.ttf",
    Path("/System/Library/Fonts/Supplemental/Impact.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Black.ttf"),
    Path("/usr/share/fonts/opentype/urw-base35/NimbusSansNarrow-Bold.otf"),
    *BOLD_FONT_CANDIDATES,
)
RUN_FONT_CANDIDATES = (
    Path("/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Black.ttf"),
    Path("/usr/share/fonts/opentype/urw-base35/NimbusSansNarrow-BoldOblique.otf"),
    *BOLD_FONT_CANDIDATES,
)
METRIC_FONT_CANDIDATES = (
    Path("/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf"),
    Path("/System/Library/Fonts/SFNSRounded.ttf"),
    Path("/System/Library/Fonts/SFCompactRounded.ttf"),
    *BOLD_FONT_CANDIDATES,
)
UNICODE_FONT_CANDIDATES = (
    ASSETS_DIR / "fonts" / "Lato-Bold.ttf",
    Path("/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc"),
    Path("/System/Library/Fonts/Kohinoor.ttc"),
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    *BOLD_FONT_CANDIDATES,
)
FONT_FAMILIES = {
    "regular": REGULAR_FONT_CANDIDATES,
    "bold": BOLD_FONT_CANDIDATES,
    "display": DISPLAY_FONT_CANDIDATES,
    "run": RUN_FONT_CANDIDATES,
    "metric": METRIC_FONT_CANDIDATES,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path, help="RM portrait image")
    parser.add_argument("--rm-name", required=True, help="RM name")
    parser.add_argument("--total-runs", required=True, help="Total runs, optionally comma-separated")
    parser.add_argument("--bm")
    parser.add_argument("--ch")
    parser.add_argument("--zh")
    parser.add_argument("--sh")
    parser.add_argument("--rh")
    parser.add_argument("--branch")
    parser.add_argument("--total-fees", "--subscription", dest="total_fees")
    parser.add_argument("--reward-points")
    parser.add_argument("--ftd-runs")
    parser.add_argument("--ftd-subscription")
    parser.add_argument("--portrait-zoom", type=float, default=1.0)
    parser.add_argument("--portrait-x", type=float, default=0.0, help="Horizontal shift as a fraction of the photo frame")
    parser.add_argument("--portrait-y", type=float, default=0.0, help="Vertical shift as a fraction of the photo frame")
    parser.add_argument(
        "--gradient",
        choices=sorted(GRADIENT_PALETTES),
        help="Optional gradient palette override; defaults to a random curated palette",
    )
    output_group = parser.add_mutually_exclusive_group(required=True)
    output_group.add_argument("--output", type=Path)
    output_group.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def runs_as_int(raw: str) -> int:
    cleaned = raw.replace(",", "").strip()
    if not re.fullmatch(r"\d+", cleaned):
        raise ValueError("total runs must be a non-negative whole number")
    return int(cleaned)


def normalize_fee_value(raw: str) -> str:
    """Use the designer's compact uppercase lakh format for numeric fees."""
    value = raw.strip().upper()
    numeric = re.fullmatch(r"(₹|RS\.?|INR)?\s*([\d,]+(?:\.\d+)?)", value)
    if numeric:
        amount = Decimal(numeric[2].replace(",", ""))
        if amount >= 100_000:
            lakhs = format(amount / Decimal(100_000), 'f')
            if '.' in lakhs:
                lakhs = lakhs.rstrip('0').rstrip('.')
            return f"{'₹' if numeric[1] else ''}{lakhs} LAKH"
    return re.sub(r"(?<=\d)\s*(?:L|LAC|LACS|LAKHS)$", " LAKH", value)


def select_layout(layouts: dict, runs: int) -> tuple[str, dict]:
    for slab, layout in layouts.items():
        upper = layout["max_runs"]
        if runs >= layout["min_runs"] and (upper is None or runs <= upper):
            return slab, layout
    raise ValueError(f"no supplied template covers {runs} runs")


def scale_layout(layout: dict, size: tuple[int, int]) -> dict:
    """Map reference design coordinates to the original full-resolution PNG."""
    factor = size[0] / layout["reference_size"][0]
    if abs(size[1] / layout["reference_size"][1] - factor) > 0.01:
        raise ValueError("Template aspect ratio does not match the configured layout")
    lengths = {"photo_radius", "font_size", "max_width", "line_height", "bottom", "extrusion", "stroke"}
    vectors = {"xy", "start", "photo_box", "shadow"}

    def scaled(value, key=""):
        if key == "optional_metric_clear":
            return {k: [[round(v * factor) for v in box] for box in boxes] for k, boxes in value.items()}
        if isinstance(value, dict):
            return {k: scaled(v, k) for k, v in value.items()}
        if key in lengths and isinstance(value, (int, float)):
            return round(value * factor)
        if key in vectors:
            return [round(v * factor) for v in value]
        if key == "clear_boxes":
            return [[round(v * factor) for v in box] for box in value]
        return value

    return scaled(layout)


def resolve_font(size: int, text: str = "", family: str = "bold") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = UNICODE_FONT_CANDIDATES if "₹" in text else FONT_FAMILIES.get(family, BOLD_FONT_CANDIDATES)
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default(size=size)


def fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    start_size: int,
    stroke: int,
    family: str,
) -> ImageFont.ImageFont:
    size = start_size
    while size >= 18:
        font = resolve_font(size, text, family)
        box = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
        if box[2] - box[0] <= max_width:
            return font
        size -= 2
    return resolve_font(18, text, family)


def draw_fitted(draw: ImageDraw.ImageDraw, text: str, spec: dict, default_color: str = "#FFFFFF") -> None:
    stroke = int(spec.get("stroke", 2))
    font = fit_font(
        draw,
        text,
        int(spec["max_width"]),
        int(spec["font_size"]),
        stroke,
        spec.get("font", "bold"),
    )
    xy = tuple(spec["xy"])
    anchor = spec.get("anchor", "mm")
    shadow = spec.get("shadow")
    if shadow:
        draw.text(
            (xy[0] + int(shadow[0]), xy[1] + int(shadow[1])),
            text,
            font=font,
            fill=spec.get("shadow_color", "#17120B"),
            anchor=anchor,
            align="center",
            stroke_width=stroke,
            stroke_fill=spec.get("shadow_color", "#17120B"),
        )
    extrusion = int(spec.get("extrusion", 0))
    if extrusion:
        extrusion_color = spec.get("extrusion_color", spec.get("shadow_color", "#17120B"))
        for depth in range(extrusion, 0, -1):
            draw.text(
                (xy[0] + depth, xy[1] + depth),
                text,
                font=font,
                fill=extrusion_color,
                anchor=anchor,
                align="center",
                stroke_width=stroke,
                stroke_fill=extrusion_color,
            )
    highlight = spec.get("highlight_color")
    if highlight:
        draw.text(
            (xy[0] - 2, xy[1] - 2),
            text,
            font=font,
            fill=highlight,
            anchor=anchor,
            align="center",
            stroke_width=stroke,
            stroke_fill=spec.get("stroke_color", "#111111"),
        )
    draw.text(
        xy,
        text,
        font=font,
        fill=spec.get("color", default_color),
        anchor=anchor,
        align="center",
        stroke_width=stroke,
        stroke_fill=spec.get("stroke_color", "#111111"),
    )


def draw_rm_name(draw: ImageDraw.ImageDraw, name: str, spec: dict) -> None:
    """Keep short names large and wrap long names over no more than two strong lines."""
    words = name.upper().split()
    if len(words) < 2 or spec.get("max_lines") == 1:
        draw_fitted(draw, name.upper(), spec)
        return

    start_size = int(spec["font_size"])
    max_width = int(spec["max_width"])
    full_font = resolve_font(start_size, name, spec.get("font", "display"))
    full_box = draw.textbbox((0, 0), name.upper(), font=full_font, stroke_width=int(spec.get("stroke", 1)))
    if full_box[2] - full_box[0] <= max_width:
        draw_fitted(draw, name.upper(), spec)
        return

    candidates: list[tuple[int, str, str]] = []
    for index in range(1, len(words)):
        first, second = " ".join(words[:index]), " ".join(words[index:])
        widths = []
        for line in (first, second):
            box = draw.textbbox((0, 0), line, font=full_font, stroke_width=int(spec.get("stroke", 1)))
            widths.append(box[2] - box[0])
        candidates.append((max(widths), first, second))
    _, first, second = min(candidates, key=lambda item: item[0])
    line_gap = max(42, round(start_size * 1.04))
    first_spec = dict(spec)
    second_spec = dict(spec)
    first_spec["xy"] = [int(spec["xy"][0]), int(spec["xy"][1]) - line_gap // 2]
    second_spec["xy"] = [int(spec["xy"][0]), int(spec["xy"][1]) + line_gap // 2]
    draw_fitted(draw, first, first_spec)
    draw_fitted(draw, second, second_spec)


def clear_metadata_area(canvas: Image.Image, spec: dict) -> None:
    x1, y1, x2, y2 = spec["clear_box"]
    width, height = x2 - x1, y2 - y1
    strip = min(int(spec.get("sample_width", 32)), max(1, width // 4))
    left = canvas.crop((x1, y1, x1 + strip, y2)).resize((1, height), Image.Resampling.BOX)
    right = canvas.crop((x2 - strip, y1, x2, y2)).resize((1, height), Image.Resampling.BOX)
    gradient = Image.new("RGB", (2, height))
    gradient.paste(left, (0, 0))
    gradient.paste(right, (1, 0))
    patch = gradient.resize((width, height), Image.Resampling.BILINEAR)
    patch = patch.filter(ImageFilter.GaussianBlur(radius=float(spec.get("clear_blur", 12))))
    canvas.paste(patch, (x1, y1))


def clear_flattened_region(canvas: Image.Image, box: list[int], sample_width: int = 20) -> None:
    """Remove flattened template text by interpolating the untouched side pixels."""
    x1, y1, x2, y2 = box
    width, height = x2 - x1, y2 - y1
    strip = min(sample_width, max(1, width // 5))
    left = canvas.crop((x1, y1, x1 + strip, y2)).resize((1, height), Image.Resampling.BOX)
    right = canvas.crop((x2 - strip, y1, x2, y2)).resize((1, height), Image.Resampling.BOX)
    gradient = Image.new("RGB", (2, height))
    gradient.paste(left, (0, 0))
    gradient.paste(right, (1, 0))
    patch = gradient.resize((width, height), Image.Resampling.BILINEAR)
    patch = patch.filter(ImageFilter.GaussianBlur(radius=8))
    canvas.paste(patch, (x1, y1))


def clear_absent_metric_labels(canvas: Image.Image, supplied: dict[str, str], layout: dict) -> list[str]:
    """Hide baked optional headings and separators when their values are absent."""
    cleared: list[str] = []
    for key, boxes in layout.get("optional_metric_clear", {}).items():
        if supplied.get(key):
            continue
        for box in boxes:
            clear_flattened_region(canvas, box)
        cleared.append(key)
    return cleared


def draw_metadata(draw: ImageDraw.ImageDraw, supplied: dict[str, str], spec: dict) -> list[str]:
    labels = {
        "bm": "BM",
        "ch": "CH",
        "zh": "ZH",
        "sh": "SH",
        "rh": "RH",
        "branch": "Branch",
        "reward_points": "Reward Points",
        "ftd_runs": "FTD Runs",
        "ftd_subscription": "FTD Subscription",
    }
    rendered: list[str] = []
    x, y = spec["start"]
    active = [key for key in spec["order"] if supplied.get(key)]
    paired = [key for key in spec.get("pair_last", []) if key in active]
    rows = [[key] for key in active if key not in paired]
    if paired:
        rows.append(paired)
    available_height = max(1, int(spec["bottom"]) - int(y))
    line_height = int(spec["line_height"])
    if len(rows) > 1:
        line_height = min(line_height, available_height // (len(rows) - 1))
    font_size = min(int(spec["font_size"]), max(18, round(line_height * 0.8)))
    for row in rows:
        text = " | ".join(f"{labels[key]}: {supplied[key]}" for key in row)
        if spec.get("uppercase"):
            text = text.upper()
        line_spec = {
            "xy": [x, y],
            "max_width": spec["max_width"],
            "font_size": font_size,
            "font": spec.get("font", "regular"),
            "color": spec.get("color", "#FFFFFF"),
            "anchor": "lm",
            "stroke": 0,
        }
        if "reward_points" in row:
            # Keep the optional reward line prominent while fitting all team rows.
            line_spec["color"] = "#241044"
            line_spec["font"] = "bold"
            padding = max(4, round(font_size * 0.23))
            draw.rounded_rectangle(
                (x - padding, y - round(font_size * 0.56), x + int(spec["max_width"]) + padding, y + round(font_size * 0.56)),
                radius=padding,
                fill="#F5C92F",
            )
        draw_fitted(draw, text, line_spec)
        rendered.extend(row)
        y += line_height
    return rendered


def make_gradient(size: tuple[int, int], colors: tuple[str, str]) -> Image.Image:
    width, height = size
    start = tuple(int(colors[0][index:index + 2], 16) for index in (1, 3, 5))
    end = tuple(int(colors[1][index:index + 2], 16) for index in (1, 3, 5))
    angle = secrets.SystemRandom().uniform(-0.55, 0.55)
    dx, dy = math.cos(angle), math.sin(angle)
    corners = (0, width * dx, height * dy, width * dx + height * dy)
    low, high = min(corners), max(corners)
    image = Image.new("RGB", size)
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            amount = ((x * dx + y * dy) - low) / max(1.0, high - low)
            amount = max(0.0, min(1.0, amount))
            pixels[x, y] = tuple(round(start[channel] * (1 - amount) + end[channel] * amount) for channel in range(3))
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_radius = int(min(size) * 0.55)
    glow_draw.ellipse(
        (width // 2 - glow_radius, -glow_radius // 2, width // 2 + glow_radius, glow_radius * 3 // 2),
        fill=(255, 255, 255, 32),
    )
    return Image.alpha_composite(image.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(28))).convert("RGB")


def remove_portrait_background(source: Image.Image) -> Image.Image:
    alpha = source.getchannel("A")
    if alpha.getextrema()[0] < 250:
        return source
    try:
        from rembg import new_session, remove
    except (ImportError, RuntimeError) as exc:
        raise RuntimeError("automatic background removal requires the rembg package") from exc
    session = new_session("u2net_human_seg")
    try:
        result = remove(
            source,
            session=session,
            alpha_matting=True,
            alpha_matting_foreground_threshold=235,
            alpha_matting_background_threshold=12,
            alpha_matting_erode_size=4,
            post_process_mask=True,
            decontaminate=True,
        )
    except Exception:
        # A basic mask is preferable to failing the entire creative if alpha matting
        # cannot solve an unusually small or low-contrast portrait.
        result = remove(source, session=session, post_process_mask=True)
    cutout = result.convert("RGBA")
    alpha = cutout.getchannel("A")
    alpha = alpha.filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.GaussianBlur(0.45))
    cutout.putalpha(alpha)
    return cutout


def detect_face_box(source: Image.Image) -> tuple[int, int, int, int] | None:
    """Find the face before segmentation, without cropping away hair or shoulders."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    preview = source.copy()
    preview.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
    rgb = np.asarray(preview.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(48, 48))
    if len(faces) == 0:
        return None
    face_x, face_y, face_w, face_h = max(faces, key=lambda face: int(face[2]) * int(face[3]))
    sx, sy = source.width / preview.width, source.height / preview.height
    return (round(face_x * sx), round(face_y * sy), round(face_w * sx), round(face_h * sy))


def fit_portrait(
    portrait: Image.Image,
    frame_size: tuple[int, int],
    face: tuple[int, int, int, int] | None,
    zoom: float = 1.0,
    shift_x: float = 0.0,
    shift_y: float = 0.0,
    face_fraction: float = 0.48,
    face_position: float = 0.46,
) -> tuple[Image.Image, tuple[int, int]]:
    """Center the face, allowing wide shoulders to extend beyond the frame."""
    width, height = frame_size
    # Ignore faint segmentation residue when finding the visible subject bounds.
    subject_box = portrait.getchannel("A").point(lambda value: 255 if value > 24 else 0).getbbox()
    if subject_box is None:
        raise ValueError("background removal could not detect a person in the RM image")
    portrait = portrait.crop(subject_box)
    if face is not None:
        face_x, face_y, face_w, face_h = face
        center_x = face_x + face_w / 2 - subject_box[0]
        center_y = face_y + face_h / 2 - subject_box[1]
        target_y = height * face_position
        scale = min(height * face_fraction / max(1, face_h), width * 0.66 / max(1, face_w))
        # Keep the top of the visible hair inside the frame with some headroom.
        if center_y > 0:
            scale = min(scale, (target_y - height * 0.07) / center_y)
        scale *= zoom
        offset = (round(width / 2 - center_x * scale), round(target_y - center_y * scale))
    else:
        # For profiles or undetected faces, center the upper subject rather than
        # an asymmetric body bounding box, and retain a little space above the head.
        scale = max(width * 0.98 / portrait.width, height * 1.30 / portrait.height) * zoom
        head_alpha = portrait.getchannel("A").crop((0, 0, portrait.width, max(1, round(portrait.height * 0.4))))
        columns = head_alpha.resize((portrait.width, 1), Image.Resampling.BOX)
        weights = list(columns.getdata())
        total = sum(weights)
        center_x = sum((x + 0.5) * weight for x, weight in enumerate(weights)) / total if total else portrait.width / 2
        offset = (round(width / 2 - center_x * scale), round(height * 0.07))
    offset = (offset[0] + round(width * shift_x), offset[1] + round(height * shift_y))
    portrait = portrait.resize(
        (max(1, round(portrait.width * scale)), max(1, round(portrait.height * scale))),
        Image.Resampling.LANCZOS,
    )
    return portrait, offset


def place_portrait(
    canvas: Image.Image,
    portrait_path: Path,
    box: list[int],
    radius: int,
    gradient_name: str,
    shape: str = "rectangle",
    background: str = "template",
    zoom: float = 1.0,
    shift_x: float = 0.0,
    shift_y: float = 0.0,
) -> None:
    x1, y1, x2, y2 = box
    width, height = x2 - x1, y2 - y1
    with Image.open(portrait_path) as source:
        portrait = ImageOps.exif_transpose(source).convert("RGBA")
    face = detect_face_box(portrait)
    portrait = remove_portrait_background(portrait)
    portrait, offset = fit_portrait(
        portrait, (width, height), face, zoom, shift_x, shift_y,
        face_fraction=0.48 if shape == "circle" else 0.44,
        face_position=0.46 if shape == "circle" else 0.44,
    )

    fitted = (
        canvas.crop((x1, y1, x2, y2)).convert("RGBA")
        if background == "template"
        else make_gradient((width, height), GRADIENT_PALETTES[gradient_name]).convert("RGBA")
    )
    shadow_alpha = Image.new("L", (width, height), 0)
    shadow_alpha.paste(portrait.getchannel("A"), (offset[0] + 5, offset[1] + 7))
    shadow_alpha = shadow_alpha.filter(ImageFilter.GaussianBlur(10))
    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 105))
    fitted.alpha_composite(Image.composite(shadow, Image.new("RGBA", (width, height)), shadow_alpha))
    fitted.alpha_composite(portrait, offset)
    mask = Image.new("L", (width, height), 0)
    mask_draw = ImageDraw.Draw(mask)
    if shape == "circle":
        mask_draw.ellipse((0, 0, width - 1, height - 1), fill=255)
    else:
        mask_draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
    canvas.paste(fitted.convert("RGB"), (x1, y1), mask)


def automatic_output(directory: Path, name: str, runs: int) -> Path:
    safe_name = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "rm"
    return directory / f"hdfc-festive-bonanza-{safe_name}-{runs}-runs.png"


def main() -> int:
    args = parse_args()
    if not 0.7 <= args.portrait_zoom <= 1.8 or not -0.3 <= args.portrait_x <= 0.3 or not -0.3 <= args.portrait_y <= 0.3:
        raise ValueError("Portrait zoom must be 0.7–1.8; position offsets must be -0.3–0.3")
    if not args.image.is_file():
        raise ValueError(f"RM image does not exist: {args.image}")
    rm_name = args.rm_name.strip()
    if not rm_name:
        raise ValueError("RM name cannot be blank")

    runs = runs_as_int(args.total_runs)
    layouts = json.loads(LAYOUTS_PATH.read_text(encoding="utf-8"))
    slab, layout = select_layout(layouts, runs)
    template_path = ASSETS_DIR / layout["template"]
    canvas = Image.open(template_path).convert("RGB")
    layout = scale_layout(layout, canvas.size)
    for box in layout.get("clear_boxes", []):
        clear_flattened_region(canvas, box)
    gradient_name = args.gradient or secrets.choice(tuple(GRADIENT_PALETTES))
    place_portrait(
        canvas,
        args.image,
        layout["photo_box"],
        int(layout.get("photo_radius", 8)),
        gradient_name,
        layout.get("photo_shape", "rectangle"),
        layout.get("photo_background", "template"),
        args.portrait_zoom,
        args.portrait_x,
        args.portrait_y,
    )

    supplied = {
        "total_runs": args.total_runs.strip(),
        "total_fees": args.total_fees,
        "reward_points": args.reward_points,
        "ftd_runs": args.ftd_runs,
        "ftd_subscription": args.ftd_subscription,
        "bm": args.bm,
        "ch": args.ch,
        "zh": args.zh,
        "sh": args.sh,
        "rh": args.rh,
        "branch": args.branch,
    }
    supplied = {key: str(value).strip() for key, value in supplied.items() if value is not None and str(value).strip()}
    cleared_metrics = clear_absent_metric_labels(canvas, supplied, layout)

    draw = ImageDraw.Draw(canvas)
    draw_rm_name(draw, rm_name, layout["name"])
    rendered: list[str] = draw_metadata(draw, supplied, layout["metadata"])
    unsupported: list[str] = [key for key in ("ftd_runs", "ftd_subscription") if supplied.get(key)]
    for key in ("total_runs", "total_fees", "reward_points"):
        value = supplied.get(key)
        if not value or key in rendered:
            continue
        spec = layout["values"].get(key)
        if spec is None:
            unsupported.append(key)
            continue
        if key == "total_fees":
            value = normalize_fee_value(value)
        display_value = f"{spec.get('prefix', '')}{value}"
        draw_fitted(draw, display_value, spec)
        rendered.append(key)

    output = args.output or automatic_output(args.output_dir, rm_name, runs)
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() != ".png":
        output = output.with_suffix(".png")
    canvas.save(output, format="PNG", optimize=True)
    print(json.dumps({
        "output": str(output),
        "slab": slab,
        "template": str(template_path),
        "gradient": gradient_name if layout.get("photo_background") != "template" else None,
        "contest": "HDFC Festive Bonanza",
        "background_removed": True,
        "rendered_fields": rendered,
        "unsupported_fields": unsupported,
        "cleared_optional_metrics": cleared_metrics,
    }, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
