#!/usr/bin/env python3
"""Render a Monsoon Bonanza creative from a slab template."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import secrets
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
except ImportError as exc:
    raise SystemExit("Pillow is required. Install it with: python3 -m pip install Pillow") from exc


SKILL_DIR = Path(__file__).resolve().parent.parent
LAYOUTS_PATH = SKILL_DIR / "references" / "template-layouts.json"
ASSETS_DIR = SKILL_DIR / "assets"
os.environ.setdefault("NUMBA_CACHE_DIR", "/tmp/monsoon-rembg-numba")
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
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
)
BOLD_FONT_CANDIDATES = (
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
)
DISPLAY_FONT_CANDIDATES = (
    Path("/System/Library/Fonts/Supplemental/Impact.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Black.ttf"),
    *BOLD_FONT_CANDIDATES,
)
RUN_FONT_CANDIDATES = (
    Path("/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Black.ttf"),
    *BOLD_FONT_CANDIDATES,
)
METRIC_FONT_CANDIDATES = (
    Path("/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf"),
    Path("/System/Library/Fonts/SFNSRounded.ttf"),
    Path("/System/Library/Fonts/SFCompactRounded.ttf"),
    *BOLD_FONT_CANDIDATES,
)
UNICODE_FONT_CANDIDATES = (
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
    """Expand a terminal numeric L abbreviation without altering other formatting."""
    return re.sub(r"(?i)(?<=\d)\s*L$", " Lakh", raw.strip())


def select_layout(layouts: dict, runs: int) -> tuple[str, dict]:
    for slab, layout in layouts.items():
        upper = layout["max_runs"]
        if runs >= layout["min_runs"] and (upper is None or runs <= upper):
            return slab, layout
    raise ValueError(f"no supplied template covers {runs} runs; the minimum supported value is 100")


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
        "ftd_runs": "FTD Runs",
        "ftd_subscription": "FTD Subscription",
    }
    rendered: list[str] = []
    x, y = spec["start"]
    order = list(spec["order"])
    if "zh" not in order:
        insert_at = order.index("ch") + 1 if "ch" in order else len(order)
        order.insert(insert_at, "zh")
    order.extend(["ftd_runs", "ftd_subscription"])
    active = [key for key in order if supplied.get(key)]
    bottom_padding = max(36, round(int(spec["font_size"]) * 1.1))
    available_height = max(1, int(spec["clear_box"][3]) - int(y) - bottom_padding)
    line_height = int(spec["line_height"])
    if len(active) > 1:
        line_height = min(line_height, available_height // (len(active) - 1))
    font_size = min(int(spec["font_size"]), max(22, round(line_height * 0.58)))
    for key in active:
        value = supplied.get(key)
        line_spec = {
            "xy": [x, y],
            "max_width": spec["max_width"],
            "font_size": font_size,
            "font": "regular",
            "color": spec.get("color", "#FFFFFF"),
            "anchor": "lm",
            "stroke": 0,
        }
        draw_fitted(draw, f"{labels[key]}: {value}", line_spec)
        rendered.append(key)
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
    result = remove(source, session=session)
    return result.convert("RGBA")


def face_focused_crop(source: Image.Image, target_ratio: float) -> Image.Image:
    try:
        import cv2
        import numpy as np
    except ImportError:
        return source
    rgb = np.asarray(source.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(48, 48))
    if len(faces) == 0:
        return source
    face_x, face_y, face_w, face_h = max(faces, key=lambda face: int(face[2]) * int(face[3]))
    crop_width = min(source.width, round(face_w * 3.15))
    crop_height = min(source.height, round(crop_width / target_ratio))
    center_x = face_x + face_w / 2
    top = round(face_y - face_h * 0.62)
    left = round(center_x - crop_width / 2)
    left = max(0, min(left, source.width - crop_width))
    top = max(0, min(top, source.height - crop_height))
    return source.crop((left, top, left + crop_width, top + crop_height))


def place_portrait(
    canvas: Image.Image,
    portrait_path: Path,
    box: list[int],
    radius: int,
    gradient_name: str,
) -> None:
    x1, y1, x2, y2 = box
    width, height = x2 - x1, y2 - y1
    with Image.open(portrait_path) as source:
        portrait = ImageOps.exif_transpose(source).convert("RGBA")
    portrait = face_focused_crop(portrait, width / height)
    portrait = remove_portrait_background(portrait)
    subject_box = portrait.getchannel("A").getbbox()
    if subject_box is None:
        raise ValueError("background removal could not detect a person in the RM image")
    portrait = portrait.crop(subject_box)
    max_size = (round(width * 0.96), round(height * 0.98))
    scale = min(max_size[0] / portrait.width, max_size[1] / portrait.height)
    scale = min(scale, 2.1)
    portrait = portrait.resize(
        (max(1, round(portrait.width * scale)), max(1, round(portrait.height * scale))),
        Image.Resampling.LANCZOS,
    )

    fitted = make_gradient((width, height), GRADIENT_PALETTES[gradient_name]).convert("RGBA")
    offset = ((width - portrait.width) // 2, height - portrait.height)
    shadow_alpha = Image.new("L", (width, height), 0)
    shadow_alpha.paste(portrait.getchannel("A"), (offset[0] + 5, max(0, offset[1] + 7)))
    shadow_alpha = shadow_alpha.filter(ImageFilter.GaussianBlur(10))
    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 105))
    fitted.alpha_composite(Image.composite(shadow, Image.new("RGBA", (width, height)), shadow_alpha))
    fitted.alpha_composite(portrait, offset)
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
    canvas.paste(fitted.convert("RGB"), (x1, y1), mask)


def automatic_output(directory: Path, name: str, runs: int) -> Path:
    safe_name = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "rm"
    return directory / f"monsoon-bonanza-{safe_name}-{runs}-runs.png"


def main() -> int:
    args = parse_args()
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
    gradient_name = args.gradient or secrets.choice(tuple(GRADIENT_PALETTES))
    place_portrait(
        canvas,
        args.image,
        layout["photo_box"],
        int(layout.get("photo_radius", 8)),
        gradient_name,
    )
    clear_metadata_area(canvas, layout["metadata"])

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
    draw_fitted(draw, rm_name.upper(), layout["name"])
    rendered: list[str] = draw_metadata(draw, supplied, layout["metadata"])
    unsupported: list[str] = []
    for key in ("total_runs", "total_fees", "reward_points"):
        value = supplied.get(key)
        if not value:
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
        "gradient": gradient_name,
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
