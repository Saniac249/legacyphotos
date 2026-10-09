#!/usr/bin/env python3

import argparse
import json
import statistics
import tempfile
from collections import defaultdict
from pathlib import Path

from recover_library import (
    ensure_renderer,
    render_effect,
    compare_images,
    find_original,
    SUPPORTED_EFFECTS,
)


def main():
    parser = argparse.ArgumentParser(
        description="Audit LegacyPhotos reconstruction against Apple FullSizeRender files."
    )

    parser.add_argument(
        "--manifest",
        required=True,
        type=Path,
    )

    parser.add_argument(
        "--mutations",
        required=True,
        type=Path,
    )

    parser.add_argument(
        "--originals",
        required=True,
        type=Path,
    )

    parser.add_argument(
        "--renderer",
        type=Path,
        default=Path("render_effect.swift"),
    )

    parser.add_argument(
        "--effect",
        type=str,
        default=None,
        help="Optional effect filter, e.g. Chrome",
    )

    parser.add_argument(
        "--orientation",
        type=int,
        default=None,
        help="Optional EXIF orientation filter",
    )

    args = parser.parse_args()

    with args.manifest.open() as f:
        manifest = json.load(f)

    renderer_binary = (
        Path(__file__).resolve().parent
        / ".build"
        / "legacyphotos-renderer"
    )

    ensure_renderer(
        args.renderer.resolve(),
        renderer_binary,
    )

    candidates = []

    for r in manifest["results"]:
        if r.get("source") != "apple_fullsize":
            continue

        if not r.get("effect"):
            continue

        if r["effect"] not in SUPPORTED_EFFECTS:
            continue

        if args.effect and r["effect"] != args.effect:
            continue

        if (
            args.orientation is not None
            and r.get("orientation") != args.orientation
        ):
            continue

        candidates.append(r)

    print()
    print("=" * 72)
    print("LegacyPhotos — Full-size reconstruction audit")
    print("=" * 72)
    print(f"Assets to test: {len(candidates)}")
    print()

    results = []

    with tempfile.TemporaryDirectory(
        prefix="legacyphotos-audit-"
    ) as tmp:
        tmp = Path(tmp)

        for index, r in enumerate(candidates, 1):
            asset = r["asset"]
            folder = r["dcim_folder"]
            effect = r["effect"]
            orientation = int(r.get("orientation") or 1)

            original, error = find_original(
                args.originals.resolve(),
                folder,
                asset,
            )

            full_render = (
                args.mutations.resolve()
                / folder
                / asset
                / "Adjustments"
                / "FullSizeRender.jpg"
            )

            print(
                f"[{index}/{len(candidates)}] "
                f"{folder}/{asset} "
                f"{effect} o={orientation}",
                end=" ... ",
                flush=True,
            )

            if not original:
                print(f" SKIP ({error})")
                continue

            if not full_render.exists():
                print(" SKIP (FullSizeRender missing)")
                continue

            ours = tmp / f"{folder}-{asset}.jpg"

            try:
                render_effect(
                    renderer_binary,
                    original,
                    effect,
                    orientation,
                    ours,
                )

                metrics = compare_images(
                    ours,
                    full_render,
                )

                item = {
                    "asset": asset,
                    "folder": folder,
                    "effect": effect,
                    "effect_version": r.get(
                        "effect_version"
                    ),
                    "orientation": orientation,
                    "mae": metrics["mae"],
                    "rmse": metrics["rmse"],
                    "psnr": metrics["psnr"],
                }

                results.append(item)

                print(
                    f"MAE={metrics['mae']:.3f} "
                    f"RMSE={metrics['rmse']:.3f}"
                )

            except Exception as exc:
                print(f" ERROR: {exc}")

    if not results:
        print("\nNo audit results.")
        return

    print()
    print("=" * 72)
    print("Overall")
    print("=" * 72)

    maes = [r["mae"] for r in results]
    rmses = [r["rmse"] for r in results]

    print(f"Tested:       {len(results)}")
    print(f"Average MAE:  {statistics.mean(maes):.3f}")
    print(f"Median MAE:   {statistics.median(maes):.3f}")
    print(f"Worst MAE:    {max(maes):.3f}")

    print()
    print(f"Average RMSE: {statistics.mean(rmses):.3f}")
    print(f"Median RMSE:  {statistics.median(rmses):.3f}")
    print(f"Best RMSE:    {min(rmses):.3f}")
    print(f"Worst RMSE:   {max(rmses):.3f}")

    groups = defaultdict(list)

    for r in results:
        key = (
            r["effect"],
            r["effect_version"],
            r["orientation"],
        )
        groups[key].append(r)

    print()
    print("=" * 72)
    print("By effect/version/orientation")
    print("=" * 72)

    for key, items in sorted(groups.items()):
        effect, version, orientation = key

        group_rmses = [
            item["rmse"]
            for item in items
        ]

        print()
        print(
            f"{effect} "
            f"v{version} "
            f"orientation={orientation}"
        )
        print(f"  n:            {len(items)}")
        print(
            f"  mean RMSE:    "
            f"{statistics.mean(group_rmses):.3f}"
        )
        print(
            f"  median RMSE:  "
            f"{statistics.median(group_rmses):.3f}"
        )
        print(
            f"  worst RMSE:   "
            f"{max(group_rmses):.3f}"
        )

    print()
    print("=" * 72)
    print("Worst 10")
    print("=" * 72)

    for r in sorted(
        results,
        key=lambda x: x["rmse"],
        reverse=True,
    )[:10]:
        print(
            f"{r['folder']}/{r['asset']}  "
            f"{r['effect']} "
            f"v{r['effect_version']} "
            f"o={r['orientation']}  "
            f"MAE={r['mae']:.3f}  "
            f"RMSE={r['rmse']:.3f}"
        )


if __name__ == "__main__":
    main()