#!/usr/bin/env python3

import argparse
import gzip
import json
import plistlib
import sqlite3
import zlib

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


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


APPLE_EPOCH = datetime(
    2001,
    1,
    1,
    tzinfo=timezone.utc,
)


def apple_timestamp(value):
    if value is None:
        return None

    try:
        seconds = float(value)

        return (
            APPLE_EPOCH
            + timedelta(seconds=seconds)
        ).isoformat()

    except (
        TypeError,
        ValueError,
        OverflowError,
    ):
        return None


def decompress_adjustment_data(data):
    attempts = []

    for offset in range(0, min(17, len(data))):
        chunk = data[offset:]

        # Raw DEFLATE
        try:
            decoded = zlib.decompress(
                chunk,
                -zlib.MAX_WBITS,
            )

            attempts.append(
                ("raw-deflate", offset, decoded)
            )
        except Exception:
            pass

        # Normal zlib
        try:
            decoded = zlib.decompress(chunk)

            attempts.append(
                ("zlib", offset, decoded)
            )
        except Exception:
            pass

        # gzip
        try:
            decoded = gzip.decompress(chunk)

            attempts.append(
                ("gzip", offset, decoded)
            )
        except Exception:
            pass

    return attempts


def decode_adjustment_plist(path):
    with path.open("rb") as f:
        outer = plistlib.load(f)

    data = outer.get("adjustmentData")

    result = {
        "editor_bundle_id": outer.get(
            "adjustmentEditorBundleID"
        ),
        "format_identifier": outer.get(
            "adjustmentFormatIdentifier"
        ),
        "format_version": outer.get(
            "adjustmentFormatVersion"
        ),
        "recipe": None,
    }

    if not isinstance(data, bytes):
        return result

    # Try direct JSON first.
    try:
        result["recipe"] = json.loads(
            data.decode("utf-8")
        )
        return result
    except Exception:
        pass

    # Legacy iOS 9 camera adjustmentData
    # has already proven to use raw DEFLATE.
    for compression, offset, decoded in (
        decompress_adjustment_data(data)
    ):
        try:
            recipe = json.loads(
                decoded.decode("utf-8")
            )

            result["recipe"] = recipe
            result["compression"] = compression
            result["compression_offset"] = offset

            return result
        except Exception:
            pass

    return result


def inspect_recipe(plist_path):
    try:
        decoded = decode_adjustment_plist(
            plist_path
        )
    except Exception as exc:
        return {
            "classification": "decode_error",
            "error": str(exc),
        }

    recipe = decoded.get("recipe")

    if not isinstance(recipe, dict):
        return {
            "classification": "undecodable",
        }

    adjustments = recipe.get(
        "adjustments",
        [],
    )

    enabled = [
        adjustment
        for adjustment in adjustments
        if adjustment.get("enabled", True)
    ]

    identifiers = [
        adjustment.get("identifier")
        for adjustment in enabled
        if adjustment.get("identifier")
    ]

    effect_adjustments = [
        adjustment
        for adjustment in enabled
        if adjustment.get("identifier") == "Effect"
    ]

    non_effect = [
        identifier
        for identifier in identifiers
        if identifier != "Effect"
    ]

    if not effect_adjustments:
        return {
            "classification": (
                "camera_adjustment_non_filter"
            ),
            "identifiers": identifiers,
        }

    effect = effect_adjustments[0]
    settings = effect.get(
        "settings",
        {},
    )

    effect_name = settings.get(
        "effectName"
    )

    effect_version = settings.get(
        "effectVersion"
    )

    metadata = recipe.get(
        "metadata",
        {},
    )

    if non_effect:
        classification = (
            "filter_plus_other_adjustments"
        )
    elif effect_name in SUPPORTED_EFFECTS:
        classification = "supported_filter"
    else:
        classification = "unknown_filter"

    return {
        "classification": classification,
        "effect": effect_name,
        "effect_version": effect_version,
        "orientation": metadata.get(
            "orientation"
        ),
        "identifiers": identifiers,
    }


def mutation_plist_path(
    mutations_root,
    directory,
    filename,
):
    directory_path = Path(directory)

    parts = list(
        directory_path.parts
    )

    # DB gives:
    # DCIM/101APPLE
    #
    # mutations_root already points to:
    # .../Mutations/DCIM
    if parts and parts[0].upper() == "DCIM":
        parts = parts[1:]

    stem = Path(filename).stem

    return (
        mutations_root
        .joinpath(*parts)
        / stem
        / "Adjustments"
        / "Adjustments.plist"
    )


def load_assets(db_path):
    connection = sqlite3.connect(
        str(db_path)
    )

    connection.row_factory = sqlite3.Row

    query = """
    SELECT
        g.Z_PK AS asset_pk,
        g.ZUUID AS uuid,
        g.ZDIRECTORY AS directory,
        g.ZFILENAME AS filename,
        g.ZDATECREATED AS date_created,
        g.ZADDEDDATE AS date_added,
        g.ZADJUSTMENTTIMESTAMP
            AS adjustment_timestamp,
        g.ZHASADJUSTMENTS
            AS has_adjustments,
        g.ZKIND AS kind,
        g.ZKINDSUBTYPE AS kind_subtype,

        a.ZORIGINALFILENAME
            AS original_filename,
        a.ZORIGINALWIDTH
            AS original_width,
        a.ZORIGINALHEIGHT
            AS original_height,
        a.ZORIGINALORIENTATION
            AS original_orientation,
        a.ZORIGINALFILESIZE
            AS original_file_size,
        a.ZEDITORBUNDLEID
            AS editor_bundle_id,
        a.ZORIGINALPATH
            AS original_path,
        a.ZORIGINALASSETSUUID
            AS original_asset_uuid,
        a.ZMASTERFINGERPRINT
            AS master_fingerprint,
        a.ZADJUSTEDFINGERPRINT
            AS adjusted_fingerprint

    FROM ZGENERICASSET AS g

    LEFT JOIN
        ZADDITIONALASSETATTRIBUTES AS a

        ON a.Z_PK =
           g.ZADDITIONALATTRIBUTES

    WHERE
        g.ZTRASHEDDATE IS NULL

    ORDER BY
        g.ZDATECREATED ASC
    """

    rows = connection.execute(
        query
    ).fetchall()

    connection.close()

    return rows


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Scan an iPhone 4S / iOS 9.3.6 "
            "Photos library."
        )
    )

    parser.add_argument(
        "--db",
        required=True,
        type=Path,
        help="Photos-snapshot.sqlite",
    )

    parser.add_argument(
        "--mutations",
        required=True,
        type=Path,
        help=(
            "PhotoData/Mutations/DCIM "
            "directory"
        ),
    )

    parser.add_argument(
        "--report",
        type=Path,
        default=Path(
            "library-scan.json"
        ),
    )

    args = parser.parse_args()

    db_path = args.db.resolve()
    mutations_root = (
        args.mutations.resolve()
    )
    report_path = (
        args.report.resolve()
    )

    assets = load_assets(db_path)

    results = []

    for row in assets:
        item = {
            "asset_pk": row["asset_pk"],
            "uuid": row["uuid"],
            "directory": row["directory"],
            "filename": row["filename"],
            "kind": row["kind"],
            "kind_subtype": (
                row["kind_subtype"]
            ),
            "has_adjustments": bool(
                row["has_adjustments"]
            ),
            "editor_bundle_id": (
                row["editor_bundle_id"]
            ),
            "date_created": (
                apple_timestamp(
                    row["date_created"]
                )
            ),
            "date_added": (
                apple_timestamp(
                    row["date_added"]
                )
            ),
            "original_filename": (
                row["original_filename"]
            ),
            "original_width": (
                row["original_width"]
            ),
            "original_height": (
                row["original_height"]
            ),
            "original_orientation": (
                row[
                    "original_orientation"
                ]
            ),
            "original_file_size": (
                row["original_file_size"]
            ),
        }

        # Videos etc. are indexed but not part
        # of LegacyPhotos' camera-photo flow.
        if row["kind"] != 0:
            item["classification"] = (
                "non_photo"
            )

            results.append(item)
            continue

        if not row["has_adjustments"]:
            item["classification"] = (
                "unfiltered_photo"
            )

            results.append(item)
            continue

        plist_path = mutation_plist_path(
            mutations_root,
            row["directory"],
            row["filename"],
        )

        item["adjustment_plist"] = str(
            plist_path
        )

        if not plist_path.exists():
            item["classification"] = (
                "missing_mutation"
            )

            results.append(item)
            continue

        recipe_info = inspect_recipe(
            plist_path
        )

        item.update(recipe_info)

        results.append(item)

    classifications = Counter(
        item["classification"]
        for item in results
    )

    effects = Counter(
        item.get("effect")
        for item in results
        if item.get("effect")
    )

    active_photos = sum(
        1
        for item in results
        if item["kind"] == 0
    )

    videos = sum(
        1
        for item in results
        if item["kind"] == 1
    )

    print()
    print("=" * 68)
    print(
        "LegacyPhotos — iPhone 4S Library Scan"
    )
    print("=" * 68)

    print(
        f"Active assets:          "
        f"{len(results)}"
    )

    print(
        f"Still images:           "
        f"{active_photos}"
    )

    print(
        f"Videos:                 "
        f"{videos}"
    )

    print()
    print("CLASSIFICATION")

    for name, count in (
        classifications.most_common()
    ):
        print(
            f"  {name:<31} {count}"
        )

    if effects:
        print()
        print("FILTERS")

        for name, count in (
            effects.most_common()
        ):
            print(
                f"  {name:<15} {count}"
            )

    interesting = [
        item
        for item in results
        if item["classification"]
        in {
            "missing_mutation",
            "camera_adjustment_non_filter",
            "filter_plus_other_adjustments",
            "unknown_filter",
            "decode_error",
            "undecodable",
        }
    ]

    if interesting:
        print()
        print("=" * 68)
        print(
            "Assets requiring inspection"
        )
        print("=" * 68)

        for item in interesting:
            print(
                f"{item['directory']}/"
                f"{item['filename']}  "
                f"{item['classification']}"
            )

            identifiers = item.get(
                "identifiers"
            )

            if identifiers:
                print(
                    "    adjustments: "
                    + ", ".join(identifiers)
                )

            if item.get("error"):
                print(
                    f"    error: "
                    f"{item['error']}"
                )

    report = {
        "schema_version": 1,
        "source_database": str(db_path),
        "mutations_root": str(
            mutations_root
        ),
        "summary": {
            "active_assets": len(results),
            "still_images": active_photos,
            "videos": videos,
            "classifications": dict(
                classifications
            ),
            "effects": dict(effects),
        },
        "assets": results,
    }

    report_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with report_path.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            report,
            f,
            indent=2,
        )

    print()
    print(
        f"Report: {report_path}"
    )


if __name__ == "__main__":
    main()