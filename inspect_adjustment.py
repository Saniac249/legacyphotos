#!/usr/bin/env python3

import argparse
import json
import plistlib
import pprint
import re
import zlib
from pathlib import Path


def printable_strings(data: bytes, min_length: int = 4):
    pattern = rb"[\x20-\x7E]{" + str(min_length).encode() + rb",}"
    return [
        match.decode("ascii", errors="replace")
        for match in re.findall(pattern, data)
    ]


def parse_structured(data: bytes):
    stripped = data.lstrip()

    # JSON
    try:
        text = data.decode("utf-8")
        obj = json.loads(text)
        return "JSON", obj
    except Exception:
        pass

    # Binary/XML plist
    try:
        obj = plistlib.loads(data)
        return "PLIST", obj
    except Exception:
        pass

    # Plain UTF-8 text
    try:
        text = data.decode("utf-8")
        if text.strip():
            return "TEXT", text
    except Exception:
        pass

    return None, None


def decompression_candidates(data: bytes):
    results = []
    seen = set()

    modes = [
        ("zlib", zlib.MAX_WBITS),
        ("raw-deflate", -zlib.MAX_WBITS),
        ("gzip", 16 + zlib.MAX_WBITS),
    ]

    # Apple sometimes wraps or prefixes archived data.
    # Try the blob normally, then a few byte offsets.
    max_offset = min(16, len(data) - 1)

    for offset in range(max_offset + 1):
        chunk = data[offset:]

        for mode_name, wbits in modes:
            try:
                decoded = zlib.decompress(chunk, wbits)
            except zlib.error:
                continue

            if not decoded:
                continue

            if decoded in seen:
                continue

            seen.add(decoded)
            results.append(
                {
                    "mode": mode_name,
                    "offset": offset,
                    "data": decoded,
                }
            )

    return results


def inspect_adjustment(path: Path):
    print("=" * 72)
    print("LegacyPhotos — Adjustment Inspector")
    print("=" * 72)
    print()
    print(f"File: {path}")

    with path.open("rb") as f:
        plist = plistlib.load(f)

    print()
    print("Adjustment metadata")
    print("-" * 72)

    for key in [
        "adjustmentBaseVersion",
        "adjustmentEditorBundleID",
        "adjustmentFormatIdentifier",
        "adjustmentFormatVersion",
    ]:
        print(f"{key}: {plist.get(key)!r}")

    data = plist.get("adjustmentData")

    if not isinstance(data, bytes):
        print()
        print("No binary adjustmentData found.")
        return

    print()
    print(f"adjustmentData size: {len(data)} bytes")
    print(f"first 32 bytes: {data[:32].hex()}")

    print()
    print("Raw payload inspection")
    print("-" * 72)

    kind, parsed = parse_structured(data)

    if kind:
        print(f"Raw payload is directly readable as {kind}:")
        pprint.pp(parsed, width=100)
    else:
        strings = printable_strings(data)

        if strings:
            print("Printable strings found in raw payload:")
            for value in strings:
                print(f"  {value}")
        else:
            print("No useful plaintext strings found.")

    print()
    print("Trying compression formats")
    print("-" * 72)

    candidates = decompression_candidates(data)

    if not candidates:
        print("No zlib/raw-DEFLATE/gzip payload could be decoded.")
        print()
        print("This does NOT mean the recipe is unrecoverable.")
        print("It may use an Apple archive/container format instead.")
        return

    for index, candidate in enumerate(candidates, start=1):
        decoded = candidate["data"]

        print()
        print(f"Candidate {index}")
        print(
            f"compression={candidate['mode']} "
            f"offset={candidate['offset']} "
            f"decoded_size={len(decoded)}"
        )

        kind, parsed = parse_structured(decoded)

        if kind:
            print(f"Decoded as {kind}:")
            pprint.pp(parsed, width=120)
        else:
            strings = printable_strings(decoded)

            if strings:
                print("Printable strings:")
                for value in strings:
                    print(f"  {value}")
            else:
                print("Decoded successfully, but structure is unknown.")

            print()
            print("First 128 decoded bytes:")
            print(decoded[:128].hex())


def main():
    parser = argparse.ArgumentParser(
        description="Inspect legacy iOS Photos Adjustments.plist files."
    )

    parser.add_argument(
        "plist",
        type=Path,
        help="Path to Adjustments.plist",
    )

    args = parser.parse_args()

    if not args.plist.exists():
        raise SystemExit(f"File does not exist: {args.plist}")

    inspect_adjustment(args.plist)


if __name__ == "__main__":
    main()