#!/usr/bin/env python3

from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

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


def load_rgb(path):
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    return np.asarray(img.convert("RGB")).astype(np.float64)


def fit_model(original, ours, apple):
    r = original[:, :, 0].ravel()
    g = original[:, :, 1].ravel()
    b = original[:, :, 2].ravel()

    ours_gray = ours[:, :, 0].ravel()
    apple_gray = apple[:, :, 0].ravel()

    # Model:
    #
    # Apple =
    #   a * modernMono
    # + b * (R - G)
    # + c * (B - G)
    # + d
    #
    # If b/c matter, the discrepancy depends on
    # original image colour, not just brightness.

    X = np.column_stack([
        ours_gray,
        r - g,
        b - g,
        np.ones_like(ours_gray),
    ])

    coeffs, _, _, _ = np.linalg.lstsq(
        X,
        apple_gray,
        rcond=None,
    )

    prediction = X @ coeffs

    rmse = np.sqrt(
        np.mean(
            (prediction - apple_gray) ** 2
        )
    )

    return coeffs, rmse


def test_model(coeffs, original, ours, apple):
    r = original[:, :, 0].ravel()
    g = original[:, :, 1].ravel()
    b = original[:, :, 2].ravel()

    ours_gray = ours[:, :, 0].ravel()
    apple_gray = apple[:, :, 0].ravel()

    X = np.column_stack([
        ours_gray,
        r - g,
        b - g,
        np.ones_like(ours_gray),
    ])

    prediction = X @ coeffs

    return np.sqrt(
        np.mean(
            (prediction - apple_gray) ** 2
        )
    )


def main():
    renderer = PROJECT / ".build/legacyphotos-renderer"

    ensure_renderer(
        PROJECT / "render_effect.swift",
        renderer,
    )

    output = ROOT / "LegacyPhotos-Mono-Diagnostics"
    output.mkdir(exist_ok=True)

    loaded = {}

    for folder, asset in ASSETS:
        original_path, error = find_original(
            ORIGINALS,
            folder,
            asset,
        )

        if not original_path:
            print(asset, error)
            continue

        apple_path = (
            MUTATIONS
            / folder
            / asset
            / "Adjustments"
            / "FullSizeRender.jpg"
        )

        ours_path = output / f"{asset}-ours.jpg"

        render_effect(
            renderer,
            original_path,
            "Mono",
            6,
            ours_path,
        )

        original = load_rgb(original_path)
        ours = load_rgb(ours_path)
        apple = load_rgb(apple_path)

        if original.shape != ours.shape:
            print(
                f"{asset}: dimension mismatch "
                f"original={original.shape} "
                f"ours={ours.shape}"
            )
            continue

        loaded[asset] = (
            original,
            ours,
            apple,
        )

    print()
    print("=" * 72)
    print("Fit each image individually")
    print("=" * 72)

    models = {}

    for asset, data in loaded.items():
        coeffs, rmse = fit_model(*data)
        models[asset] = coeffs

        print()
        print(asset)
        print(
            f"  modern Mono: {coeffs[0]:+.6f}"
        )
        print(
            f"  R-G term:     {coeffs[1]:+.6f}"
        )
        print(
            f"  B-G term:     {coeffs[2]:+.6f}"
        )
        print(
            f"  intercept:    {coeffs[3]:+.6f}"
        )
        print(
            f"  fitted RMSE:  {rmse:.3f}"
        )

    if len(loaded) == 2:
        a, b = loaded.keys()

        print()
        print("=" * 72)
        print("Cross-validation")
        print("=" * 72)

        rmse_ab = test_model(
            models[a],
            *loaded[b],
        )

        rmse_ba = test_model(
            models[b],
            *loaded[a],
        )

        print(
            f"Train {a} → test {b}: "
            f"RMSE {rmse_ab:.3f}"
        )

        print(
            f"Train {b} → test {a}: "
            f"RMSE {rmse_ba:.3f}"
        )


if __name__ == "__main__":
    main()