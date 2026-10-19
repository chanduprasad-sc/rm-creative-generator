#!/usr/bin/env python3
"""Streamlit Community Cloud entrypoint for the RM Creative Generator."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import requests
import streamlit as st


APP_DIR = Path(__file__).resolve().parent
RENDERER = APP_DIR / "renderer" / "generate_creative.py"
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
MAX_UPLOAD_BYTES = 12 * 1024 * 1024

EXTRACTION_TOOL = {
    "name": "extract_creative_details",
    "description": (
        "Extract only the approved fields needed for an RM appreciation creative. "
        "Ignore greetings, commentary, targets, dates, phone numbers, and all other content."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "rm_name": {"type": "string", "description": "Relationship Manager name"},
            "total_runs": {"type": "integer", "minimum": 0},
            "bm": {"type": ["string", "null"], "description": "Branch Manager name"},
            "ch": {"type": ["string", "null"], "description": "Cluster Head name"},
            "zh": {"type": ["string", "null"], "description": "Zonal Head name"},
            "rh": {"type": ["string", "null"], "description": "Regional Head name"},
            "sh": {"type": ["string", "null"], "description": "State Head name"},
            "branch": {"type": ["string", "null"]},
            "total_fees": {"type": ["string", "null"], "description": "Fees or subscription value"},
            "reward_points": {"type": ["string", "null"]},
            "ftd_runs": {"type": ["string", "null"]},
            "ftd_subscription": {"type": ["string", "null"]},
        },
        "required": ["rm_name", "total_runs"],
        "additionalProperties": False,
    },
}


class CreativeError(RuntimeError):
    """A user-facing generation failure."""


def secret(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, default)
    except FileNotFoundError:
        value = default
    return str(value).strip()


def clean_value(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip()
    return cleaned or None


def token_rates(model: str) -> tuple[float, float] | None:
    """Return standard Claude API input/output USD rates per million tokens."""
    normalized = model.lower()
    if "sonnet-5" in normalized:
        return 2.0, 10.0
    if "sonnet" in normalized:
        return 3.0, 15.0
    if "haiku-4-5" in normalized:
        return 1.0, 5.0
    return None


def extract_with_claude(details_text: str) -> tuple[dict[str, Any], dict[str, int]]:
    api_key = secret("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise CreativeError("ANTHROPIC_API_KEY has not been added to the app's Streamlit secrets.")

    model = secret("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
    payload = {
        "model": model,
        "max_tokens": 500,
        "system": (
            "You extract fields for an Indian RM performance poster. Never infer or invent missing "
            "values. Interpret RM as relationship manager, BM as branch manager, CH as cluster head, "
            "ZH as zonal head, RH as regional head, and SH as state head. Treat runs as total_runs "
            "unless explicitly labelled FTD runs. Call the supplied tool exactly once."
        ),
        "messages": [{"role": "user", "content": details_text}],
        "tools": [EXTRACTION_TOOL],
        "tool_choice": {"type": "tool", "name": EXTRACTION_TOOL["name"]},
    }

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
            timeout=60,
        )
    except requests.RequestException as exc:
        raise CreativeError(f"Could not reach Claude: {exc}") from exc

    if not response.ok:
        try:
            message = response.json().get("error", {}).get("message", response.text)
        except ValueError:
            message = response.text
        raise CreativeError(f"Claude API error: {message}")

    body = response.json()
    tool_blocks = [block for block in body.get("content", []) if block.get("type") == "tool_use"]
    if not tool_blocks:
        raise CreativeError("Claude did not return the required structured details.")

    data = tool_blocks[0].get("input", {})
    rm_name = clean_value(data.get("rm_name"))
    if not rm_name:
        raise CreativeError("Could not find the RM name in the supplied text.")
    try:
        total_runs = int(str(data.get("total_runs", "")).replace(",", ""))
    except ValueError as exc:
        raise CreativeError("Could not find a valid runs value.") from exc
    if total_runs < 0:
        raise CreativeError("Runs must be a non-negative whole number.")

    approved = {
        "rm_name": rm_name,
        "total_runs": total_runs,
        "bm": clean_value(data.get("bm")),
        "ch": clean_value(data.get("ch")),
        "zh": clean_value(data.get("zh")),
        "rh": clean_value(data.get("rh")),
        "sh": clean_value(data.get("sh")),
        "branch": clean_value(data.get("branch")),
        "total_fees": clean_value(data.get("total_fees")),
        "reward_points": clean_value(data.get("reward_points")),
        "ftd_runs": clean_value(data.get("ftd_runs")),
        "ftd_subscription": clean_value(data.get("ftd_subscription")),
    }
    usage = body.get("usage", {})
    return approved, {
        "input_tokens": int(usage.get("input_tokens", 0)),
        "output_tokens": int(usage.get("output_tokens", 0)),
    }


def render_creative(photo_path: Path, details: dict[str, Any], output_path: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        str(RENDERER),
        "--image",
        str(photo_path),
        "--rm-name",
        details["rm_name"],
        "--total-runs",
        str(details["total_runs"]),
        "--output",
        str(output_path),
    ]
    flags = {
        "bm": "--bm",
        "ch": "--ch",
        "zh": "--zh",
        "rh": "--rh",
        "sh": "--sh",
        "branch": "--branch",
        "total_fees": "--total-fees",
        "reward_points": "--reward-points",
        "ftd_runs": "--ftd-runs",
        "ftd_subscription": "--ftd-subscription",
    }
    for key, flag in flags.items():
        if details.get(key):
            command.extend([flag, str(details[key])])

    environment = os.environ.copy()
    environment.setdefault("NUMBA_CACHE_DIR", "/tmp/festive-rembg-numba")
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=240,
            env=environment,
        )
    except subprocess.TimeoutExpired as exc:
        raise CreativeError("Image processing timed out. Please try a smaller or clearer portrait.") from exc
    if result.returncode != 0:
        message = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "Image rendering failed."
        raise CreativeError(message)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"output": str(output_path)}


def generate(photo_bytes: bytes, suffix: str, details_text: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="rm-creative-") as temp_name:
        temp_dir = Path(temp_name)
        photo_path = temp_dir / f"portrait{suffix}"
        output_path = temp_dir / "creative.png"
        photo_path.write_bytes(photo_bytes)
        details, usage = extract_with_claude(details_text)
        summary = render_creative(photo_path, details, output_path)
        return {
            "image": output_path.read_bytes(),
            "details": details,
            "usage": usage,
            "model": secret("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),
            "slab": summary.get("slab"),
            "gradient": summary.get("gradient"),
            "unsupported_fields": summary.get("unsupported_fields", []),
        }


st.set_page_config(page_title="HDFC Festive Bonanza", page_icon="🏆", layout="centered")

st.title("HDFC — Festive Bonanza")
st.caption("Upload an RM portrait and paste the performance details. Only supplied fields appear on the poster.")
st.caption("Templates: 0–700 runs (purple) · Above 700 runs (brown).")

with st.form("creative_form"):
    photo = st.file_uploader("RM photo", type=["jpg", "jpeg", "png", "webp"])
    details_text = st.text_area(
        "Creative details",
        height=190,
        placeholder=(
            "RM: Meenakshi Sharma\n"
            "Runs: 1122\n"
            "Total fees: ₹2.99 L\n"
            "Reward points: 5,000\n"
            "BM: Sunil Sadarvarte\n"
            "SH: Krupa Shah\n"
            "RH: Amit Agarwal\n"
            "Branch: Thane 2"
        ),
    )
    submitted = st.form_submit_button("Generate creative", type="primary", use_container_width=True)

if submitted:
    if photo is None:
        st.error("Please upload an RM photograph.")
    elif len(details_text.strip()) < 3:
        st.error("Please enter the RM name and runs.")
    else:
        photo_bytes = photo.getvalue()
        suffix = Path(photo.name).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            st.error("Upload a JPG, PNG, or WEBP photograph.")
        elif len(photo_bytes) > MAX_UPLOAD_BYTES:
            st.error("The photograph must be smaller than 12 MB.")
        else:
            try:
                with st.spinner("Removing the background and generating the creative…"):
                    st.session_state["result"] = generate(photo_bytes, suffix, details_text)
            except CreativeError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Unexpected error: {exc}")

result = st.session_state.get("result")
if result:
    st.success(f"Creative ready — runs slab: {result.get('slab') or 'selected'}")
    st.image(result["image"], use_container_width=True)
    safe_name = re.sub(r"[^a-z0-9]+", "-", result["details"]["rm_name"].lower()).strip("-") or "rm"
    st.download_button(
        "Download PNG",
        data=result["image"],
        file_name=f"hdfc-festive-bonanza-{safe_name}-{result['details']['total_runs']}-runs.png",
        mime="image/png",
        type="primary",
        use_container_width=True,
    )
    if result.get("unsupported_fields"):
        st.info("These supplied fields do not have a place in this template: " + ", ".join(result["unsupported_fields"]))
    with st.expander("Detected details and Claude usage"):
        st.json(result["details"])
        input_tokens = result["usage"]["input_tokens"]
        output_tokens = result["usage"]["output_tokens"]
        model = result.get("model", "")
        st.write(f"Input tokens: {input_tokens:,}")
        st.write(f"Output tokens: {output_tokens:,}")
        st.write(f"Model: `{model}`")
        rates = token_rates(model)
        if rates:
            estimated_cost = (input_tokens * rates[0] + output_tokens * rates[1]) / 1_000_000
            st.write(f"Estimated Claude cost: ${estimated_cost:.6f}")
