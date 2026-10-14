#!/usr/bin/env python3

from pathlib import Path

import numpy as np
from PIL import Image

from recover_library import (
    ensure_renderer,
    render_effect,
    find_original,
)

ROOT = Path("/Users/san/Desktop")
PROJECT = Path("/Users/san/Developer/legacyphotos")

MUTATIONS = ROOT / "iPhone4S-Mutations/Mutations/DCIM"
ORIGINALS = ROOT / "iPhone4S-Originals"

ASSETS = [
    ("100APPLE", "IMG_0227"),
    ("100APPLE", "IMG_0229"),
]


def regression(x, y):
    x = x.astype(np.float64)
    y = y.astype(np.float64)

    A = np.vstack([x, np.ones_like(x)]).T
    slope, intercept = np.linalg.lstsq(A, y, rcond=None)[0]

    predicted = slope * x + intercept

    rmse = np.sqrt(
        np.mean((predicted - y) ** 2)
    )

    return slope, intercept, rmse


def main():
    renderer = PROJECT / ".build/legacyphotos-renderer"

    ensure_renderer(
        PROJECT / "render_effect.swift",
        renderer,
    )

    output = ROOT / "LegacyPhotos-Mono-Diagnostics"
    output.mkdir(exist_ok=True)

    for folder, asset in ASSETS:

        original, error = find_original(
            ORIGINALS,
            folder,
            asset,
        )

        if not original:
            print(asset, error)
            continue

        apple = (
            MUTATIONS
            / folder
            / asset
            / "Adjustments"
            / "FullSizeRender.jpg"
        )

        ours = output / f"{asset}-ours.jpg"

        render_effect(
            renderer,
            original,
            "Mono",
            6,
            ours,
        )

        ours_img = np.asarray(
            Image.open(ours).convert("RGB")
        ).astype(np.float64)

        apple_img = np.asarray(
            Image.open(apple).convert("RGB")
        ).astype(np.float64)

        print()
        print("=" * 60)
        print(asset)
        print("=" * 60)

        difference = apple_img - ours_img

        print(
            "Mean signed difference RGB:",
            difference.mean(axis=(0, 1)),
        )

        print(
            "MAE RGB:",
            np.abs(difference).mean(axis=(0, 1)),
        )

        print(
            "RMSE RGB:",
            np.sqrt(
                np.mean(
                    difference ** 2,
                    axis=(0, 1)
                )
            ),
        )

        ours_gray = ours_img.mean(axis=2).ravel()
        apple_gray = apple_img.mean(axis=2).ravel()

        slope, intercept, linear_rmse = regression(
            ours_gray,
            apple_gray,
        )

        print()
        print("Best linear tone mapping:")
        print(
            f"  Apple ≈ {slope:.6f} × ours "
            f"+ {intercept:.6f}"
        )
        print(
            f"  RMSE after linear mapping: "
            f"{linear_rmse:.3f}"
        )

        print()
        print("Tone buckets:")

        for start in range(0, 256, 32):

            end = start + 32

            mask = (
                (ours_gray >= start)
                & (ours_gray < end)
            )

            if not np.any(mask):
                continue

            delta = (
                apple_gray[mask]
                - ours_gray[mask]
            )

            print(
                f"  {start:3d}-{end-1:3d}: "
                f"mean delta {delta.mean():+.3f} "
                f"(n={mask.sum()})"
            )


if __name__ == "__main__":
    main()