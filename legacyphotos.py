#!/usr/bin/env python3

import argparse
import sys

from rich.console import Console
from rich.panel import Panel

from device import (
    DeviceError,
    UnsupportedDeviceError,
    require_supported_device,
)

from photos_db import (
    PhotosDatabaseError,
    camera_assets,
    camera_library_summary,
    prepare_snapshot,
)

from incremental_import import (
    DEFAULT_HISTORY,
    imported_uuids,
    open_history,
)


console = Console()


def get_supported_device():
    try:
        with console.status(
            "[bold]Looking for iPhone 4S..."
        ):
            return require_supported_device()

    except UnsupportedDeviceError as exc:
        console.print()
        console.print(
            Panel(
                str(exc),
                title="LegacyPhotos",
                border_style="red",
            )
        )

        return None

    except DeviceError as exc:
        console.print()
        console.print(
            f"[red]LegacyPhotos:[/red] {exc}"
        )

        return None


def status_command():
    device = get_supported_device()

    if device is None:
        return 1

    console.print()

    console.print(
        Panel(
            "\n".join(
                [
                    "[green]✓ iPhone 4S detected[/green]",
                    "",
                    f"Device:  {device.name}",
                    f"Model:   {device.product_type}",
                    f"iOS:     {device.product_version}",
                    f"Build:   {device.build_version}",
                    "",
                    "[bold]Ready to sync.[/bold]",
                ]
            ),
            title="LegacyPhotos",
        )
    )

    return 0


def scan_command():
    device = get_supported_device()

    if device is None:
        return 1

    try:
        with console.status(
            "[bold]Reading iPhone photo library..."
        ):
            snapshot, fetched = (
                prepare_snapshot()
            )

            summary = (
                camera_library_summary(
                    snapshot
                )
            )

    except PhotosDatabaseError as exc:
        console.print()
        console.print(
            f"[red]LegacyPhotos:[/red] {exc}"
        )

        return 1

    console.print()

    lines = [
        "[green]✓ iPhone 4S detected[/green]",
        f"iOS {device.product_version} · "
        f"Build {device.build_version}",
        "",
        (
            f"[bold]{summary['camera_photos']}[/bold] "
            "camera photos"
        ),
        (
            f"{summary['adjusted']} "
            "camera-adjustment records"
        ),
        (
            f"{summary['unadjusted']} "
            "without adjustments"
        ),
        "",
        (
            "Photos.sqlite: "
            "[green]✓[/green]"
        ),
        (
            "WAL: "
            + (
                "[green]✓[/green]"
                if fetched.get(
                    "Photos.sqlite-wal"
                )
                else "[dim]not present[/dim]"
            )
        ),
        (
            "SHM: "
            + (
                "[green]✓[/green]"
                if fetched.get(
                    "Photos.sqlite-shm"
                )
                else "[dim]not present[/dim]"
            )
        ),
    ]

    console.print(
        Panel(
            "\n".join(lines),
            title="LegacyPhotos",
        )
    )

    return 0

def sync_command():
    device = get_supported_device()

    if device is None:
        return 1

    try:
        with console.status(
            "[bold]Checking iPhone 4S..."
        ):
            snapshot, _ = prepare_snapshot()

            assets = camera_assets(
                snapshot
            )

            history = open_history(
                DEFAULT_HISTORY
            )

            known = imported_uuids(
                history
            )

    except PhotosDatabaseError as exc:
        console.print()
        console.print(
            f"[red]LegacyPhotos:[/red] {exc}"
        )

        return 1

    except Exception as exc:
        console.print()
        console.print(
            f"[red]LegacyPhotos:[/red] {exc}"
        )

        return 1

    finally:
        try:
            history.close()
        except Exception:
            pass

    new_assets = [
        asset
        for asset in assets
        if asset.get("uuid")
        not in known
    ]

    filtered = sum(
        1
        for asset in new_assets
        if asset.get("has_adjustments")
    )

    unfiltered = (
        len(new_assets)
        - filtered
    )

    console.print()

    lines = [
        "[green]✓ iPhone 4S[/green]",
        (
            f"iOS {device.product_version} · "
            f"Build {device.build_version}"
        ),
        "",
        (
            f"Photos on camera:       "
            f"{len(assets)}"
        ),
        (
            f"Previously imported:   "
            f"{len(known)}"
        ),
        "",
    ]

    if new_assets:
        lines.extend(
            [
                (
                    f"[bold]{len(new_assets)} "
                    "new photos[/bold]"
                ),
                "",
                (
                    f"Filtered:             "
                    f"{filtered}"
                ),
                (
                    f"Unfiltered:           "
                    f"{unfiltered}"
                ),
            ]
        )

    else:
        lines.extend(
            [
                "[green][bold]"
                "No new photos."
                "[/bold][/green]",
                "",
                "Everything is up to date.",
            ]
        )

    console.print(
        Panel(
            "\n".join(lines),
            title="LegacyPhotos",
        )
    )

    if new_assets:
        console.print()
        console.print(
            "[bold]Newest captures[/bold]"
        )

        for asset in new_assets[-10:]:
            state = (
                "filtered"
                if asset.get(
                    "has_adjustments"
                )
                else "original"
            )

            console.print(
                f"  "
                f"{asset['filename']:<14} "
                f"[dim]{state}[/dim]"
            )

        console.print()
        console.print(
            "[yellow]"
            "Importing these photos is "
            "not enabled in the single-command "
            "workflow yet."
            "[/yellow]"
        )

    return 0

def main():
    parser = argparse.ArgumentParser(
        prog="legacyphotos",
        description=(
            "Recover full-resolution filtered photos "
            "from an iPhone 4S running iOS 9.3.6."
        ),
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    subparsers.add_parser(
        "status",
        help=(
            "Detect and verify the connected "
            "iPhone 4S."
        ),
    )

    subparsers.add_parser(
        "scan",
        help=(
            "Read the current iPhone 4S "
            "photo library."
        ),
    )
    subparsers.add_parser(
    "sync",
    help=(
        "Check the iPhone for "
        "new photos."
        ),
    )

    args = parser.parse_args()

    if args.command == "status":
        return status_command()

    if args.command == "scan":
        return scan_command()
    
    if args.command == "sync":
        return sync_command()

    return 1


if __name__ == "__main__":
    sys.exit(main())