#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess

from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
)

from recover_library import (
    ensure_renderer,
    recover_asset,
)

from scan_library import inspect_recipe


console = Console()


DEFAULT_HISTORY = (
    Path.home()
    / "Library"
    / "Application Support"
    / "LegacyPhotos"
    / "history.sqlite"
)

DEFAULT_STAGE = (
    Path.home()
    / "Library"
    / "Caches"
    / "LegacyPhotos"
    / "incoming"
)


# ---------------------------------------------------------------------
# History database
# ---------------------------------------------------------------------

def open_history(path):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    connection = sqlite3.connect(
        str(path)
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS imports (
            asset_uuid TEXT PRIMARY KEY,

            directory TEXT NOT NULL,
            filename TEXT NOT NULL,

            capture_date TEXT,

            classification TEXT,
            effect TEXT,
            effect_version INTEGER,

            source TEXT,

            output_path TEXT NOT NULL,
            output_size INTEGER,
            output_sha256 TEXT,

            imported_at TEXT NOT NULL
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_imports_filename

        ON imports (
            directory,
            filename
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            started_at TEXT NOT NULL,
            finished_at TEXT,

            discovered INTEGER DEFAULT 0,
            imported INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0
        )
        """
    )

    connection.commit()

    return connection


def imported_uuids(connection):
    rows = connection.execute(
        """
        SELECT asset_uuid
        FROM imports
        """
    ).fetchall()

    return {
        row[0]
        for row in rows
    }


def mark_imported(
    connection,
    asset,
    output_path,
    classification,
    source,
    effect=None,
    effect_version=None,
):
    output_path = Path(
        output_path
    )

    sha256 = file_sha256(
        output_path
    )

    connection.execute(
        """
        INSERT OR REPLACE INTO imports (
            asset_uuid,
            directory,
            filename,
            capture_date,
            classification,
            effect,
            effect_version,
            source,
            output_path,
            output_size,
            output_sha256,
            imported_at
        )

        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            asset["uuid"],
            asset["directory"],
            asset["filename"],
            asset.get("date_created"),
            classification,
            effect,
            effect_version,
            source,
            str(output_path),
            output_path.stat().st_size,
            sha256,
            datetime.now(
                timezone.utc
            ).isoformat(),
        ),
    )

    connection.commit()


def start_run(connection, discovered):
    cursor = connection.execute(
        """
        INSERT INTO runs (
            started_at,
            discovered
        )

        VALUES (?, ?)
        """,
        (
            datetime.now(
                timezone.utc
            ).isoformat(),
            discovered,
        ),
    )

    connection.commit()

    return cursor.lastrowid


def finish_run(
    connection,
    run_id,
    imported,
    failed,
):
    connection.execute(
        """
        UPDATE runs

        SET
            finished_at = ?,
            imported = ?,
            failed = ?

        WHERE id = ?
        """,
        (
            datetime.now(
                timezone.utc
            ).isoformat(),
            imported,
            failed,
            run_id,
        ),
    )

    connection.commit()


# ---------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------

def file_sha256(path):
    digest = hashlib.sha256()

    with Path(path).open(
        "rb"
    ) as f:
        while True:
            chunk = f.read(
                1024 * 1024
            )

            if not chunk:
                break

            digest.update(
                chunk
            )

    return digest.hexdigest()


def load_report(path):
    with Path(path).open(
        "r",
        encoding="utf-8",
    ) as f:
        return json.load(f)


def apple_folder(asset):
    directory = (
        asset.get("directory")
        or ""
    )

    return Path(directory).name


def asset_stem(asset):
    filename = (
        asset.get("filename")
        or ""
    )

    return Path(filename).stem


def is_camera_photo(asset):
    if not isinstance(asset, dict):
        return False

    if asset.get("kind") != 0:
        return False

    filename = (
        asset.get("filename")
        or ""
    ).upper()

    directory = (
        asset.get("directory")
        or ""
    )

    if not filename.endswith(".JPG"):
        return False

    if not directory.startswith("DCIM/"):
        return False

    return True


def find_existing_original(
    root,
    asset,
):
    if root is None:
        return None

    root = Path(root)

    directory = asset[
        "directory"
    ]

    folder = apple_folder(
        asset
    )

    filename = asset[
        "filename"
    ]

    candidates = [
        root
        / directory
        / filename,

        root
        / folder
        / filename,

        root
        / filename,
    ]

    expected_size = asset.get(
        "original_file_size"
    )

    for candidate in candidates:
        if not candidate.exists():
            continue

        if expected_size:
            try:
                if (
                    candidate.stat().st_size
                    != expected_size
                ):
                    continue
            except OSError:
                continue

        return candidate

    return None


def find_existing_recovered(
    root,
    asset,
):
    if root is None:
        return None

    root = Path(root)

    folder = apple_folder(
        asset
    )

    stem = asset_stem(
        asset
    )

    candidates = [
        root
        / folder
        / f"{stem}.JPG",

        root
        / f"{stem}.JPG",
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None


# ---------------------------------------------------------------------
# Bootstrap history from the previous development recovery
# ---------------------------------------------------------------------

def bootstrap_existing(
    connection,
    assets,
    originals_root,
    recovered_root,
):
    known = imported_uuids(
        connection
    )

    added = 0

    for asset in assets:
        uuid = asset.get(
            "uuid"
        )

        if not uuid:
            continue

        if uuid in known:
            continue

        if not is_camera_photo(
            asset
        ):
            continue

        classification = asset.get(
            "classification"
        )

        existing = None
        source = None

        # A filtered capture counts as imported
        # only if its finished recovered render exists.
        if classification == "supported_filter":
            existing = (
                find_existing_recovered(
                    recovered_root,
                    asset,
                )
            )

            if existing:
                source = (
                    "bootstrap_recovered"
                )

        # An unfiltered capture is already final
        # in its original JPEG form.
        elif classification == "unfiltered_photo":
            existing = (
                find_existing_original(
                    originals_root,
                    asset,
                )
            )

            if existing:
                source = (
                    "bootstrap_original"
                )

        if not existing:
            continue

        mark_imported(
            connection=connection,
            asset=asset,
            output_path=existing,
            classification=classification,
            source=source,
            effect=asset.get(
                "effect"
            ),
            effect_version=asset.get(
                "effect_version"
            ),
        )

        known.add(
            uuid
        )

        added += 1

    return added


# ---------------------------------------------------------------------
# Staging paths
# ---------------------------------------------------------------------

def staged_original(
    stage,
    asset,
):
    return (
        stage
        / "originals"
        / apple_folder(asset)
        / asset["filename"]
    )


def staged_adjustments_dir(
    stage,
    asset,
):
    return (
        stage
        / "mutations"
        / "DCIM"
        / apple_folder(asset)
        / asset_stem(asset)
        / "Adjustments"
    )


def staged_plist(
    stage,
    asset,
):
    return (
        staged_adjustments_dir(
            stage,
            asset,
        )
        / "Adjustments.plist"
    )


# ---------------------------------------------------------------------
# AFC device sync
# ---------------------------------------------------------------------

def afc_safe_path(path):
    value = str(
        path
    )

    # Keep the first implementation predictable.
    # Our default staging path contains no spaces.
    if any(
        char.isspace()
        for char in value
    ):
        raise RuntimeError(
            "The AFC staging path cannot "
            "contain spaces in this MVP."
        )

    return value


def build_afc_commands(
    assets,
    stage,
):
    commands = []

    for asset in assets:
        original = staged_original(
            stage,
            asset,
        )

        original.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        remote_original = (
            f"{asset['directory']}/"
            f"{asset['filename']}"
        )

        commands.append(
            "get "
            f"{remote_original} "
            f"{afc_safe_path(original)}"
        )

        if not asset.get(
            "has_adjustments"
        ):
            continue

        adjustments = (
            staged_adjustments_dir(
                stage,
                asset,
            )
        )

        adjustments.mkdir(
            parents=True,
            exist_ok=True,
        )

        base = (
            "PhotoData/Mutations/"
            f"{asset['directory']}/"
            f"{asset_stem(asset)}/"
            "Adjustments"
        )

        resources = [
            "Adjustments.plist",
            "FullSizeRender.jpg",
            "SubstandardFullSizeRender.jpg",
        ]

        for resource in resources:
            local = (
                adjustments
                / resource
            )

            remote = (
                f"{base}/"
                f"{resource}"
            )

            commands.append(
                "get "
                f"{remote} "
                f"{afc_safe_path(local)}"
            )

    commands.append(
        "exit"
    )

    return commands


def sync_from_device(
    assets,
    stage,
):
    afcclient = shutil.which(
        "afcclient"
    )

    if not afcclient:
        raise RuntimeError(
            "afcclient was not found "
            "in PATH."
        )

    commands = build_afc_commands(
        assets,
        stage,
    )

    script = (
        "\n".join(
            commands
        )
        + "\n"
    )

    with console.status(
        "[bold]Fetching new captures "
        "from iPhone 4S..."
    ):
        process = subprocess.run(
            [afcclient],
            input=script,
            text=True,
            capture_output=True,
        )

    # Optional render resources may not exist,
    # so we don't trust afcclient's overall
    # return code alone. Verify mandatory
    # resources explicitly below.

    missing = []

    for asset in assets:
        original = staged_original(
            stage,
            asset,
        )

        if not original.exists():
            missing.append(
                (
                    asset,
                    "original JPEG",
                )
            )

            continue

        if asset.get(
            "has_adjustments"
        ):
            plist = staged_plist(
                stage,
                asset,
            )

            if not plist.exists():
                missing.append(
                    (
                        asset,
                        "Adjustments.plist",
                    )
                )

    return (
        missing,
        process.stdout,
        process.stderr,
    )


# ---------------------------------------------------------------------
# Final output
# ---------------------------------------------------------------------

def atomic_copy(
    source,
    destination,
    overwrite=False,
):
    source = Path(
        source
    )

    destination = Path(
        destination
    )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if (
        destination.exists()
        and not overwrite
    ):
        raise FileExistsError(
            f"Output already exists: "
            f"{destination}"
        )

    partial = destination.with_name(
        destination.name
        + ".partial"
    )

    if partial.exists():
        partial.unlink()

    shutil.copy2(
        source,
        partial,
    )

    os.replace(
        partial,
        destination,
    )


def final_output_path(
    output_root,
    asset,
):
    return (
        output_root
        / apple_folder(asset)
        / asset["filename"]
    )


def process_unfiltered(
    asset,
    stage,
    output_root,
    overwrite,
):
    original = staged_original(
        stage,
        asset,
    )

    destination = final_output_path(
        output_root,
        asset,
    )

    atomic_copy(
        original,
        destination,
        overwrite=overwrite,
    )

    return {
        "status": "recovered",
        "classification": (
            "unfiltered_photo"
        ),
        "source": "original",
        "output": destination,
        "effect": None,
        "effect_version": None,
    }


def process_filtered(
    asset,
    stage,
    output_root,
    renderer,
    overwrite,
):
    plist = staged_plist(
        stage,
        asset,
    )

    recipe = inspect_recipe(
        plist
    )

    classification = (
        recipe.get(
            "classification"
        )
    )

    if classification != "supported_filter":
        return {
            "status": "unsupported",
            "classification": classification,
            "source": None,
            "output": None,
            "effect": recipe.get(
                "effect"
            ),
            "effect_version": (
                recipe.get(
                    "effect_version"
                )
            ),
        }

    adjustments_dir = (
        staged_adjustments_dir(
            stage,
            asset,
        )
    )

    temp_output_root = (
        stage
        / "rendered"
    )

    result = recover_asset(
        adjustments_dir=adjustments_dir,
        mutations_root=(
            stage
            / "mutations"
            / "DCIM"
        ),
        originals_root=(
            stage
            / "originals"
        ),
        output_root=temp_output_root,
        renderer=renderer,
        save_diffs=False,
        copy_metadata=True,
        overwrite=True,
    )

    if (
        result.get(
            "status"
        )
        != "recovered"
    ):
        return {
            "status": result.get(
                "status",
                "error",
            ),
            "classification": (
                classification
            ),
            "source": result.get(
                "source"
            ),
            "output": None,
            "effect": recipe.get(
                "effect"
            ),
            "effect_version": (
                recipe.get(
                    "effect_version"
                )
            ),
            "notes": result.get(
                "notes",
                [],
            ),
        }

    rendered = (
        temp_output_root
        / apple_folder(asset)
        / (
            asset_stem(asset)
            + ".JPG"
        )
    )

    if not rendered.exists():
        return {
            "status": "error",
            "classification": (
                classification
            ),
            "source": result.get(
                "source"
            ),
            "output": None,
            "effect": recipe.get(
                "effect"
            ),
            "effect_version": (
                recipe.get(
                    "effect_version"
                )
            ),
            "notes": [
                "Recovered render was "
                "not found on disk."
            ],
        }

    destination = final_output_path(
        output_root,
        asset,
    )

    atomic_copy(
        rendered,
        destination,
        overwrite=overwrite,
    )

    return {
        "status": "recovered",
        "classification": (
            classification
        ),
        "source": result.get(
            "source",
            "reconstructed",
        ),
        "output": destination,
        "effect": recipe.get(
            "effect"
        ),
        "effect_version": (
            recipe.get(
                "effect_version"
            )
        ),
    }


# ---------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------

def cleanup_asset_stage(
    stage,
    asset,
):
    original = staged_original(
        stage,
        asset,
    )

    if original.exists():
        original.unlink()

    mutation_asset = (
        stage
        / "mutations"
        / "DCIM"
        / apple_folder(asset)
        / asset_stem(asset)
    )

    if mutation_asset.exists():
        shutil.rmtree(
            mutation_asset,
            ignore_errors=True,
        )

    rendered_asset = (
        stage
        / "rendered"
        / apple_folder(asset)
        / (
            asset_stem(asset)
            + ".JPG"
        )
    )

    if rendered_asset.exists():
        rendered_asset.unlink()


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Incrementally import new "
            "iPhone 4S captures."
        )
    )

    parser.add_argument(
        "--report",
        required=True,
        type=Path,
        help=(
            "library-scan.json produced "
            "by scan_library.py"
        ),
    )

    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help=(
            "Final LegacyPhotos import "
            "directory"
        ),
    )

    parser.add_argument(
        "--history",
        type=Path,
        default=DEFAULT_HISTORY,
    )

    parser.add_argument(
        "--stage",
        type=Path,
        default=DEFAULT_STAGE,
    )

    parser.add_argument(
        "--renderer",
        type=Path,
        default=Path(
            "render_effect.swift"
        ),
    )

    parser.add_argument(
        "--bootstrap-existing",
        action="store_true",
        help=(
            "Seed history from the "
            "existing old library."
        ),
    )

    parser.add_argument(
        "--bootstrap-originals",
        type=Path,
        default=None,
    )

    parser.add_argument(
        "--bootstrap-recovered",
        type=Path,
        default=None,
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
    )

    args = parser.parse_args()

    report = load_report(
        args.report
    )

    assets = [
        asset
        for asset
        in report["assets"]
        if is_camera_photo(asset)
    ]

    history = open_history(
        args.history.expanduser()
    )

    if args.bootstrap_existing:
        console.print(
            "\n[bold]Bootstrapping "
            "existing imports...[/bold]"
        )

        added = bootstrap_existing(
            connection=history,
            assets=assets,
            originals_root=(
                args.bootstrap_originals
            ),
            recovered_root=(
                args.bootstrap_recovered
            ),
        )

        console.print(
            f"Added to history: {added}"
        )

    known = imported_uuids(
        history
    )

    new_assets = [
        asset
        for asset in assets
        if asset.get(
            "uuid"
        )
        not in known
    ]

    new_assets.sort(
        key=lambda item: (
            item.get(
                "date_created"
            )
            or ""
        )
    )

    filtered = sum(
        1
        for asset in new_assets
        if asset.get(
            "has_adjustments"
        )
    )

    unfiltered = (
        len(new_assets)
        - filtered
    )

    console.print()
    console.rule(
        "[bold]LegacyPhotos[/bold]"
    )

    console.print(
        f"Camera photos in library: "
        f"{len(assets)}"
    )

    console.print(
        f"Previously imported:       "
        f"{len(known)}"
    )

    console.print(
        f"[bold]New photos:              "
        f"{len(new_assets)}[/bold]"
    )

    if new_assets:
        console.print(
            f"  Filtered:               "
            f"{filtered}"
        )

        console.print(
            f"  Unfiltered:             "
            f"{unfiltered}"
        )

    if not new_assets:
        console.print(
            "\n[green]Nothing new "
            "to import.[/green]"
        )

        history.close()
        return

    console.print()
    console.print(
        "[bold]Newest pending captures[/bold]"
    )

    for asset in new_assets[-10:]:
        marker = (
            "filtered"
            if asset.get(
                "has_adjustments"
            )
            else "original"
        )

        console.print(
            f"  {asset['filename']:<14} "
            f"{marker}"
        )

    if args.dry_run:
        console.print(
            "\n[yellow]Dry run only — "
            "nothing was fetched or "
            "imported.[/yellow]"
        )

        history.close()
        return

    stage = (
        args.stage
        .expanduser()
        .resolve()
    )

    output_root = (
        args.output
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
        f"Fetching {len(new_assets)} "
        "new captures..."
    )

    missing, afc_stdout, afc_stderr = (
        sync_from_device(
            new_assets,
            stage,
        )
    )

    missing_uuids = {
        asset["uuid"]
        for asset, resource
        in missing
    }

    if missing:
        console.print()
        console.print(
            "[yellow]Some required "
            "resources could not be "
            "fetched:[/yellow]"
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

    ensure_renderer(
        args.renderer.resolve(),
        renderer_binary,
    )

    run_id = start_run(
        history,
        len(new_assets),
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
            total=len(
                new_assets
            ),
        )

        for asset in new_assets:
            progress.update(
                task,
                description=(
                    f"Importing "
                    f"{asset['filename']}"
                ),
            )

            if (
                asset["uuid"]
                in missing_uuids
            ):
                failed_count += 1

                progress.advance(
                    task
                )

                continue

            try:
                if asset.get(
                    "has_adjustments"
                ):
                    result = (
                        process_filtered(
                            asset=asset,
                            stage=stage,
                            output_root=(
                                output_root
                            ),
                            renderer=(
                                renderer_binary
                            ),
                            overwrite=(
                                args.overwrite
                            ),
                        )
                    )

                else:
                    result = (
                        process_unfiltered(
                            asset=asset,
                            stage=stage,
                            output_root=(
                                output_root
                            ),
                            overwrite=(
                                args.overwrite
                            ),
                        )
                    )

                if (
                    result.get(
                        "status"
                    )
                    == "recovered"
                ):
                    mark_imported(
                        connection=history,
                        asset=asset,
                        output_path=(
                            result["output"]
                        ),
                        classification=(
                            result[
                                "classification"
                            ]
                        ),
                        source=(
                            result[
                                "source"
                            ]
                        ),
                        effect=(
                            result.get(
                                "effect"
                            )
                        ),
                        effect_version=(
                            result.get(
                                "effect_version"
                            )
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

            progress.advance(
                task
            )

    finish_run(
        history,
        run_id,
        imported_count,
        failed_count,
    )

    history.close()

    console.print()
    console.rule(
        "[bold]Import complete[/bold]"
    )

    console.print(
        f"Imported: {imported_count}"
    )

    console.print(
        f"Failed:   {failed_count}"
    )

    console.print(
        f"History:  "
        f"{args.history.expanduser()}"
    )

    console.print(
        f"Output:   "
        f"{output_root}"
    )

    if failed_count:
        console.print()
        console.print(
            "[yellow]"
            "Failed assets remain "
            "unmarked in history and "
            "will be retried next time."
            "[/yellow]"
        )


if __name__ == "__main__":
    main()