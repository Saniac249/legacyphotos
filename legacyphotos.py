#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
)
from rich.prompt import Confirm

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

from recover_library import (
    ensure_renderer,
)

from scan_library import (
    apple_timestamp,
)

from incremental_import import (
    DEFAULT_HISTORY,
    DEFAULT_STAGE,
    cleanup_asset_stage,
    finish_run,
    imported_uuids,
    mark_imported,
    open_history,
    process_filtered,
    process_unfiltered,
    start_run,
    sync_from_device,
)


console = Console()


DEFAULT_OUTPUT = (
    Path.home()
    / "Desktop"
    / "LegacyPhotos-Imported"
)


# ---------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------

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


# ---------------------------------------------------------------------
# Asset helpers
# ---------------------------------------------------------------------

def normalize_asset(asset):
    """
    Convert database timestamps into the same ISO format
    used by our existing history database.
    """

    asset = dict(asset)

    asset["date_created"] = apple_timestamp(
        asset.get("date_created")
    )

    asset["date_added"] = apple_timestamp(
        asset.get("date_added")
    )

    return asset


def new_assets_for_history(
    assets,
    known,
):
    return [
        normalize_asset(asset)
        for asset in assets
        if asset.get("uuid")
        and asset["uuid"] not in known
    ]


# ---------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------

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


# ---------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------

def scan_command():
    device = get_supported_device()

    if device is None:
        return 1

    try:
        with console.status(
            "[bold]Reading iPhone photo library..."
        ):
            snapshot, fetched = prepare_snapshot()

            summary = camera_library_summary(
                snapshot
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
        (
            f"iOS {device.product_version} · "
            f"Build {device.build_version}"
        ),
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
        "Photos.sqlite: [green]✓[/green]",
        (
            "WAL: "
            + (
                "[green]✓[/green]"
                if fetched.get("Photos.sqlite-wal")
                else "[dim]not present[/dim]"
            )
        ),
        (
            "SHM: "
            + (
                "[green]✓[/green]"
                if fetched.get("Photos.sqlite-shm")
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


# ---------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------

def import_new_assets(
    assets,
    history,
    output_root,
):
    stage = (
        DEFAULT_STAGE
        .expanduser()
        .resolve()
    )

    output_root = (
        Path(output_root)
        .expanduser()
        .resolve()
    )

    stage.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    console.print()
    console.print(
        f"Fetching {len(assets)} "
        "new captures from iPhone 4S..."
    )

    missing, _, _ = sync_from_device(
        assets,
        stage,
    )

    missing_uuids = {
        asset["uuid"]
        for asset, _
        in missing
    }

    if missing:
        console.print()

        console.print(
            "[yellow]"
            "Some required resources "
            "could not be fetched:"
            "[/yellow]"
        )

        for asset, resource in missing:
            console.print(
                f"  {asset['filename']}: "
                f"{resource}"
            )

    renderer_binary = (
        Path(__file__)
        .resolve()
        .parent
        / ".build"
        / "legacyphotos-renderer"
    )

    renderer_source = (
        Path(__file__)
        .resolve()
        .parent
        / "render_effect.swift"
    )

    ensure_renderer(
        renderer_source,
        renderer_binary,
    )

    run_id = start_run(
        history,
        len(assets),
    )

    imported_count = 0
    failed_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn(
            "[bold]{task.description}"
        ),
        BarColumn(
            bar_width=None
        ),
        TaskProgressColumn(),
        console=console,
    ) as progress:

        task = progress.add_task(
            "Importing...",
            total=len(assets),
        )

        for asset in assets:
            progress.update(
                task,
                description=(
                    f"Importing "
                    f"{asset['filename']}"
                ),
            )

            if asset["uuid"] in missing_uuids:
                failed_count += 1
                progress.advance(task)
                continue

            try:
                if asset.get(
                    "has_adjustments"
                ):
                    result = process_filtered(
                        asset=asset,
                        stage=stage,
                        output_root=output_root,
                        renderer=renderer_binary,
                        overwrite=False,
                    )

                else:
                    result = process_unfiltered(
                        asset=asset,
                        stage=stage,
                        output_root=output_root,
                        overwrite=False,
                    )

                if result.get("status") == "recovered":
                    mark_imported(
                        connection=history,
                        asset=asset,
                        output_path=result["output"],
                        classification=result[
                            "classification"
                        ],
                        source=result["source"],
                        effect=result.get(
                            "effect"
                        ),
                        effect_version=result.get(
                            "effect_version"
                        ),
                    )

                    cleanup_asset_stage(
                        stage,
                        asset,
                    )

                    imported_count += 1

                else:
                    failed_count += 1

                    console.print(
                        "\n[yellow]"
                        f"{asset['filename']} "
                        f"was not imported: "
                        f"{result.get('status')}"
                        "[/yellow]"
                    )

            except Exception as exc:
                failed_count += 1

                console.print(
                    "\n[red]"
                    f"{asset['filename']} "
                    f"failed: {exc}"
                    "[/red]"
                )

            progress.advance(task)

    finish_run(
        history,
        run_id,
        imported_count,
        failed_count,
    )

    return (
        imported_count,
        failed_count,
        output_root,
    )


# ---------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------

def sync_command(
    output_root,
    assume_yes=False,
):
    device = get_supported_device()

    if device is None:
        return 1

    history = None

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

        if history is not None:
            history.close()

        return 1

    except Exception as exc:
        console.print()

        console.print(
            f"[red]LegacyPhotos:[/red] {exc}"
        )

        if history is not None:
            history.close()

        return 1

    new_assets = new_assets_for_history(
        assets,
        known,
    )

    adjusted = sum(
        1
        for asset in new_assets
        if asset.get("has_adjustments")
    )

    originals = (
        len(new_assets)
        - adjusted
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
            f"Photos on camera:      "
            f"{len(assets)}"
        ),
        (
            f"Previously imported:  "
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
                    f"Filtered:            "
                    f"{adjusted}"
                ),
                (
                    f"Unfiltered:          "
                    f"{originals}"
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

    if not new_assets:
        history.close()
        return 0

    console.print()
    console.print(
        "[bold]New captures[/bold]"
    )

    for asset in new_assets:
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

    if not assume_yes:
        console.print()

        confirmed = Confirm.ask(
            f"Import all "
            f"{len(new_assets)} "
            f"new photos?",
            default=True,
        )

        if not confirmed:
            history.close()

            console.print(
                "\nImport cancelled."
            )

            return 0

    try:
        (
            imported_count,
            failed_count,
            final_output,
        ) = import_new_assets(
            assets=new_assets,
            history=history,
            output_root=output_root,
        )

    finally:
        history.close()

    console.print()
    console.rule(
        "[bold]Import complete[/bold]"
    )

    if imported_count:
        console.print(
            f"[green]Imported: "
            f"{imported_count}[/green]"
        )
    else:
        console.print(
            "Imported: 0"
        )

    if failed_count:
        console.print(
            f"[red]Failed:   "
            f"{failed_count}[/red]"
        )
    else:
        console.print(
            "Failed:   0"
        )

    console.print(
        f"Output:   {final_output}"
    )

    if failed_count:
        console.print()
        console.print(
            "[yellow]"
            "Failed photos were not added "
            "to import history and will be "
            "retried next time."
            "[/yellow]"
        )

        return 1

    return 0


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

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

    sync_parser = subparsers.add_parser(
        "sync",
        help=(
            "Import new photos from "
            "the iPhone 4S."
        ),
    )

    sync_parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=(
            "Destination for imported photos."
        ),
    )

    sync_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help=(
            "Import without asking "
            "for confirmation."
        ),
    )

    args = parser.parse_args()

    if args.command == "status":
        return status_command()

    if args.command == "scan":
        return scan_command()

    if args.command == "sync":
        return sync_command(
            output_root=args.output,
            assume_yes=args.yes,
        )

    return 1


if __name__ == "__main__":
    sys.exit(main())