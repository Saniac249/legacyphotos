#!/usr/bin/env python3

import argparse
import json
import math
import plistlib
import shutil
import subprocess
import sys
import zlib
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image, ImageChops, ImageStat


SUPPORTED_EFFECTS = {
    "Chrome",
    "Fade",
    "Instant",
    "Mono",
    "Noir",
    "Process",
    "Tonal",
    "Transfer",
}


# ---------------------------------------------------------------------
# Adjustment decoding
# ---------------------------------------------------------------------

def decompress_adjustment_data(data: bytes) -> bytes:
    """
    Apple legacy com.apple.photo adjustmentData commonly uses raw DEFLATE.
    Try the known/common variants so we fail gracefully on other versions.
    """

    modes = [
        -zlib.MAX_WBITS,          # raw DEFLATE
        zlib.MAX_WBITS,           # zlib
        16 + zlib.MAX_WBITS,      # gzip
    ]

    max_offset = min(16, max(0, len(data) - 1))

    for offset in range(max_offset + 1):
        chunk = data[offset:]

        for wbits in modes:
            try:
                decoded = zlib.decompress(chunk, wbits)

                if decoded:
                    return decoded
            except zlib.error:
                continue

    raise ValueError("Could not decompress adjustmentData")


def decode_adjustments(plist_path: Path) -> dict:
    with plist_path.open("rb") as f:
        plist = plistlib.load(f)

    data = plist.get("adjustmentData")

    if not isinstance(data, bytes):
        raise ValueError("No binary adjustmentData found")

    decoded = decompress_adjustment_data(data)

    try:
        recipe = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise ValueError(
            f"Decoded adjustmentData is not valid JSON: {exc}"
        ) from exc

    return {
        "container": plist,
        "recipe": recipe,
    }


def enabled_adjustments(recipe: dict) -> list[dict]:
    return [
        item
        for item in recipe.get("adjustments", [])
        if item.get("enabled", True)
    ]


def get_effect_info(recipe: dict):
    """
    Return:
      effect_name
      effect_version
      unsupported_adjustments
    """

    effect_name = None
    effect_version = None
    unsupported = []

    for item in enabled_adjustments(recipe):
        identifier = item.get("identifier")

        if identifier == "Effect":
            settings = item.get("settings", {})
            effect_name = settings.get("effectName")
            effect_version = settings.get("effectVersion")
        else:
            unsupported.append(identifier or "<unknown>")

    return effect_name, effect_version, unsupported


# ---------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------

def find_case_insensitive(directory: Path, filename: str):
    if not directory.exists():
        return None

    wanted = filename.lower()

    for child in directory.iterdir():
        if child.is_file() and child.name.lower() == wanted:
            return child

    return None


def image_extensions():
    return {
        ".jpg",
        ".jpeg",
        ".png",
        ".heic",
        ".tif",
        ".tiff",
    }


def find_original(
    originals_root: Path,
    apple_folder: str,
    asset_name: str,
):
    """
    Try structured Image Capture/DCIM layout first, then flattened exports.

    Examples:
      Originals/101APPLE/IMG_1153.JPG
      Originals/IMG_1153.JPG
    """

    possible_names = [
        f"{asset_name}.JPG",
        f"{asset_name}.JPEG",
        f"{asset_name}.jpg",
        f"{asset_name}.jpeg",
    ]

    # Preferred: preserved DCIM layout.
    for name in possible_names:
        candidate = originals_root / apple_folder / name

        if candidate.exists():
            return candidate, None

    # Common Image Capture flattened layout.
    for name in possible_names:
        candidate = originals_root / name

        if candidate.exists():
            return candidate, None

    # Last resort: recursive basename search.
    matches = []

    for path in originals_root.rglob("*"):
        if not path.is_file():
            continue

        if path.suffix.lower() not in image_extensions():
            continue

        if path.stem.lower() == asset_name.lower():
            matches.append(path)

    if len(matches) == 1:
        return matches[0], None

    if len(matches) > 1:
        return None, (
            f"Multiple possible originals found: "
            + ", ".join(str(x) for x in matches[:5])
        )

    return None, "Original not found"


# ---------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------

def ensure_renderer(
    swift_source: Path,
    binary_path: Path,
):
    """
    Compile Swift renderer once instead of interpreting it for every photo.
    """

    if not swift_source.exists():
        raise FileNotFoundError(
            f"Renderer source missing: {swift_source}"
        )

    needs_compile = (
        not binary_path.exists()
        or swift_source.stat().st_mtime > binary_path.stat().st_mtime
    )

    if not needs_compile:
        return

    binary_path.parent.mkdir(parents=True, exist_ok=True)

    print("Compiling Core Image renderer...")

    result = subprocess.run(
        [
            "swiftc",
            str(swift_source),
            "-o",
            str(binary_path),
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)

        raise RuntimeError("Swift renderer compilation failed")


def render_effect(
    renderer: Path,
    original: Path,
    effect_name: str,
    orientation: int,
    output: Path,
):
    output.parent.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [
            str(renderer),
            str(original),
            str(effect_name),
            str(orientation),
            str(output),
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip()
            or result.stdout.strip()
            or "Renderer failed"
        )


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------

def compare_images(
    ours_path: Path,
    reference_path: Path,
    diff_output: Optional[Path] = None,
):
    with Image.open(ours_path) as ours_img:
        ours = ours_img.convert("RGB")

    with Image.open(reference_path) as ref_img:
        reference = ref_img.convert("RGB")

    original_ours_size = ours.size
    reference_size = reference.size

    resized = original_ours_size != reference_size

    if resized:
        ours = ours.resize(
            reference_size,
            Image.Resampling.LANCZOS,
        )

    diff = ImageChops.difference(ours, reference)

    stats = ImageStat.Stat(diff)

    mae = sum(stats.mean) / len(stats.mean)

    rmse = math.sqrt(
        sum(value ** 2 for value in stats.rms)
        / len(stats.rms)
    )

    if rmse == 0:
        psnr = float("inf")
    else:
        psnr = 20 * math.log10(255.0 / rmse)

    if diff_output is not None:
        diff_output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        diff.save(diff_output)

    # Conservative initial thresholds.
    #
    # We'll refine these after testing more devices / iOS versions.
    if mae <= 5 and rmse <= 10:
        confidence = "high"
    elif mae <= 12 and rmse <= 20:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "ours_size": list(original_ours_size),
        "reference_size": list(reference_size),
        "reference_was_resized": resized,
        "mae": mae,
        "rmse": rmse,
        "psnr": psnr,
        "confidence": confidence,
    }


# ---------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------

def copy_safe_metadata(
    original: Path,
    output: Path,
):
    """
    Copy archival/camera metadata without copying orientation or pixel
    dimensions that could incorrectly describe our physically-rendered output.
    """

    exiftool = shutil.which("exiftool")

    if not exiftool:
        return False, "exiftool not installed"

    tags = [
        "-EXIF:DateTimeOriginal",
        "-EXIF:CreateDate",
        "-EXIF:ModifyDate",
        "-EXIF:Make",
        "-EXIF:Model",
        "-EXIF:Software",
        "-EXIF:FNumber",
        "-EXIF:ExposureTime",
        "-EXIF:ISO",
        "-EXIF:FocalLength",
        "-EXIF:FocalLengthIn35mmFormat",
        "-GPS:all",
    ]

    result = subprocess.run(
        [
            exiftool,
            "-overwrite_original",
            "-TagsFromFile",
            str(original),
            *tags,
            str(output),
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return False, (
            result.stderr.strip()
            or result.stdout.strip()
        )

    return True, None


# ---------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------

def recover_asset(
    adjustments_dir: Path,
    mutations_root: Path,
    originals_root: Path,
    output_root: Path,
    renderer: Path,
    save_diffs: bool,
    copy_metadata: bool,
    overwrite: bool,
):
    asset_dir = adjustments_dir.parent
    apple_dir = asset_dir.parent

    asset_name = asset_dir.name
    apple_folder = apple_dir.name

    plist_path = find_case_insensitive(
        adjustments_dir,
        "Adjustments.plist",
    )

    full_render = find_case_insensitive(
        adjustments_dir,
        "FullSizeRender.jpg",
    )

    preview_render = find_case_insensitive(
        adjustments_dir,
        "SubstandardFullSizeRender.jpg",
    )

    output_dir = output_root / apple_folder
    output_path = output_dir / f"{asset_name}.JPG"

    result = {
        "asset": asset_name,
        "dcim_folder": apple_folder,
        "adjustments_path": str(adjustments_dir),
        "original": None,
        "effect": None,
        "effect_version": None,
        "orientation": None,
        "master_width": None,
        "master_height": None,
        "source": None,
        "output": None,
        "status": None,
        "validation": None,
        "unsupported_adjustments": [],
        "notes": [],
    }

    if output_path.exists() and not overwrite:
        result["status"] = "skipped_existing"
        result["output"] = str(output_path)
        return result

    original, original_error = find_original(
        originals_root,
        apple_folder,
        asset_name,
    )

    if original:
        result["original"] = str(original)
    elif original_error:
        result["notes"].append(original_error)

    recipe = None

    if plist_path:
        try:
            decoded = decode_adjustments(plist_path)
            recipe = decoded["recipe"]

            metadata = recipe.get("metadata", {})

            result["orientation"] = metadata.get(
                "orientation",
                1,
            )
            result["master_width"] = metadata.get(
                "masterWidth"
            )
            result["master_height"] = metadata.get(
                "masterHeight"
            )

            effect_name, effect_version, unsupported = (
                get_effect_info(recipe)
            )

            result["effect"] = effect_name
            result["effect_version"] = effect_version
            result["unsupported_adjustments"] = unsupported

        except Exception as exc:
            result["notes"].append(
                f"Adjustment decode failed: {exc}"
            )
    else:
        result["notes"].append(
            "Adjustments.plist not found"
        )

    # -------------------------------------------------------------
    # Best possible outcome:
    # Apple already created a full-resolution edited render.
    # Never recreate what Apple already preserved.
    # -------------------------------------------------------------

    if full_render:
        output_dir.mkdir(parents=True, exist_ok=True)

        shutil.copy2(
            full_render,
            output_path,
        )

        result["source"] = "apple_fullsize"
        result["output"] = str(output_path)
        result["status"] = "recovered"

        if original and copy_metadata:
            ok, error = copy_safe_metadata(
                original,
                output_path,
            )

            if not ok and error:
                result["notes"].append(
                    f"Metadata copy: {error}"
                )

        return result

    # -------------------------------------------------------------
    # Full render missing:
    # reconstruct from Apple's recipe.
    # -------------------------------------------------------------

    if recipe is None:
        result["status"] = "unsupported"
        result["notes"].append(
            "No decodable recipe and no Apple FullSizeRender"
        )
        return result

    if not original:
        result["status"] = "missing_original"
        return result

    effect_name = result["effect"]

    if not effect_name:
        result["status"] = "unsupported"
        result["notes"].append(
            "No Effect adjustment found"
        )
        return result

    if effect_name not in SUPPORTED_EFFECTS:
        result["status"] = "unsupported"
        result["notes"].append(
            f"Unsupported effect: {effect_name}"
        )
        return result

    if result["unsupported_adjustments"]:
        result["status"] = "unsupported"
        result["notes"].append(
            "Recipe contains adjustments not yet implemented: "
            + ", ".join(
                result["unsupported_adjustments"]
            )
        )
        return result

    orientation = int(
        result["orientation"] or 1
    )

    try:
        render_effect(
            renderer,
            original,
            effect_name,
            orientation,
            output_path,
        )
    except Exception as exc:
        result["status"] = "render_failed"
        result["notes"].append(str(exc))
        return result

    result["source"] = "reconstructed"
    result["output"] = str(output_path)
    result["status"] = "recovered"

    if original and copy_metadata:
        ok, error = copy_safe_metadata(
            original,
            output_path,
        )

        if not ok and error:
            result["notes"].append(
                f"Metadata copy: {error}"
            )

    # -------------------------------------------------------------
    # Validate reconstruction against Apple's own cached render.
    # -------------------------------------------------------------

    if preview_render:
        diff_output = None

        if save_diffs:
            diff_output = (
                output_root
                / "_diffs"
                / apple_folder
                / f"{asset_name}-diff.jpg"
            )

        try:
            validation = compare_images(
                output_path,
                preview_render,
                diff_output,
            )

            validation["reference"] = str(
                preview_render
            )

            if diff_output:
                validation["diff"] = str(
                    diff_output
                )

            result["validation"] = validation

        except Exception as exc:
            result["notes"].append(
                f"Validation failed: {exc}"
            )

    else:
        result["notes"].append(
            "No SubstandardFullSizeRender available for validation"
        )

    return result


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Recover legacy iOS Photos edits from "
            "Apple adjustment metadata."
        )
    )

    parser.add_argument(
        "--mutations",
        required=True,
        type=Path,
        help=(
            "Path to extracted PhotoData/Mutations/DCIM"
        ),
    )

    parser.add_argument(
        "--originals",
        required=True,
        type=Path,
        help=(
            "Folder containing full-resolution DCIM originals"
        ),
    )

    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Recovered-photo output directory",
    )

    parser.add_argument(
        "--renderer",
        type=Path,
        default=Path("render_effect.swift"),
        help=(
            "Swift Core Image renderer source "
            "(default: render_effect.swift)"
        ),
    )

    parser.add_argument(
        "--save-diffs",
        action="store_true",
        help=(
            "Save validation difference images"
        ),
    )

    parser.add_argument(
        "--copy-metadata",
        action="store_true",
        help=(
            "Copy safe camera/date/GPS metadata "
            "with ExifTool"
        ),
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing recovered files",
    )

    args = parser.parse_args()

    mutations_root = args.mutations.expanduser().resolve()
    originals_root = args.originals.expanduser().resolve()
    output_root = args.output.expanduser().resolve()
    swift_source = args.renderer.expanduser().resolve()

    if not mutations_root.exists():
        raise SystemExit(
            f"Mutations folder not found: {mutations_root}"
        )

    if not originals_root.exists():
        raise SystemExit(
            f"Originals folder not found: {originals_root}"
        )

    output_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    renderer_binary = (
        Path(__file__).resolve().parent
        / ".build"
        / "legacyphotos-renderer"
    )

    ensure_renderer(
        swift_source,
        renderer_binary,
    )

    adjustment_dirs = sorted(
        {
            path.parent
            for path in mutations_root.rglob(
                "Adjustments.plist"
            )
        }
    )

    print()
    print("=" * 72)
    print("LegacyPhotos Recovery")
    print("=" * 72)
    print(f"Mutations : {mutations_root}")
    print(f"Originals : {originals_root}")
    print(f"Output    : {output_root}")
    print()
    print(
        f"Found {len(adjustment_dirs)} edited assets."
    )
    print()

    results = []

    for index, adjustments_dir in enumerate(
        adjustment_dirs,
        start=1,
    ):
        asset_name = adjustments_dir.parent.name

        print(
            f"[{index}/{len(adjustment_dirs)}] "
            f"{asset_name}",
            end=" ... ",
            flush=True,
        )

        try:
            result = recover_asset(
                adjustments_dir=adjustments_dir,
                mutations_root=mutations_root,
                originals_root=originals_root,
                output_root=output_root,
                renderer=renderer_binary,
                save_diffs=args.save_diffs,
                copy_metadata=args.copy_metadata,
                overwrite=args.overwrite,
            )
        except Exception as exc:
            result = {
                "asset": asset_name,
                "status": "error",
                "notes": [str(exc)],
            }

        results.append(result)

        source = result.get("source")
        validation = result.get("validation")

        if result["status"] == "recovered":
            if source == "apple_fullsize":
                print("✓ Apple full-size render")

            elif source == "reconstructed":
                if validation:
                    print(
                        "✓ reconstructed "
                        f"({validation['confidence']} confidence, "
                        f"RMSE {validation['rmse']:.3f})"
                    )
                else:
                    print(
                        "✓ reconstructed "
                        "(unvalidated)"
                    )

            else:
                print("✓ recovered")

        else:
            notes = result.get("notes", [])

            detail = (
                notes[-1]
                if notes
                else result["status"]
            )

            print(
                f"⚠ {result['status']}: {detail}"
            )

    # -------------------------------------------------------------
    # Manifest
    # -------------------------------------------------------------

    manifest = {
        "tool": "legacyphotos",
        "generated_at": (
            datetime.now().astimezone().isoformat()
        ),
        "mutations_root": str(mutations_root),
        "originals_root": str(originals_root),
        "output_root": str(output_root),
        "results": results,
    }

    manifest_path = (
        output_root / "manifest.json"
    )

    with manifest_path.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            manifest,
            f,
            indent=2,
            ensure_ascii=False,
        )

    # -------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------

    status_counts = Counter(
        result.get("status", "unknown")
        for result in results
    )

    source_counts = Counter(
        result.get("source")
        for result in results
        if result.get("source")
    )

    print()
    print("=" * 72)
    print("Recovery complete")
    print("=" * 72)

    print(
        f"Recovered: "
        f"{status_counts.get('recovered', 0)}"
    )

    print(
        f"  Apple FullSizeRender: "
        f"{source_counts.get('apple_fullsize', 0)}"
    )

    print(
        f"  Reconstructed: "
        f"{source_counts.get('reconstructed', 0)}"
    )

    for status, count in sorted(
        status_counts.items()
    ):
        if status == "recovered":
            continue

        print(
            f"{status.replace('_', ' ').title()}: "
            f"{count}"
        )

    print()
    print(f"Manifest: {manifest_path}")
    print()


if __name__ == "__main__":
    main()