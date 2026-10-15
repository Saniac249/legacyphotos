#!/usr/bin/env python3

import shutil
import sqlite3
import subprocess

from pathlib import Path


class PhotosDatabaseError(Exception):
    pass


DEFAULT_CACHE = (
    Path.home()
    / "Library"
    / "Caches"
    / "LegacyPhotos"
    / "database"
)


REMOTE_DATABASE_FILES = [
    "Photos.sqlite",
    "Photos.sqlite-wal",
    "Photos.sqlite-shm",
]


def require_afcclient():
    path = shutil.which("afcclient")

    if not path:
        raise PhotosDatabaseError(
            "afcclient was not found in PATH."
        )

    return path


def afc_get(remote_path, local_path):
    afcclient = require_afcclient()

    local_path = Path(local_path)
    local_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if local_path.exists():
        local_path.unlink()

    # Our cache path deliberately contains no spaces,
    # keeping interaction with this older afcclient
    # implementation predictable.
    if any(
        char.isspace()
        for char in str(local_path)
    ):
        raise PhotosDatabaseError(
            "LegacyPhotos database cache path "
            "must not contain spaces."
        )

    commands = (
        f"get {remote_path} {local_path}\n"
        "exit\n"
    )

    subprocess.run(
        [afcclient],
        input=commands,
        text=True,
        capture_output=True,
    )

    return local_path.exists()


def fetch_database_files(cache_dir=DEFAULT_CACHE):
    cache_dir = (
        Path(cache_dir)
        .expanduser()
        .resolve()
    )

    cache_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    fetched = {}

    for filename in REMOTE_DATABASE_FILES:
        remote = (
            f"PhotoData/{filename}"
        )

        local = (
            cache_dir
            / filename
        )

        fetched[filename] = afc_get(
            remote,
            local,
        )

    if not fetched["Photos.sqlite"]:
        raise PhotosDatabaseError(
            "Unable to fetch PhotoData/Photos.sqlite "
            "from the iPhone."
        )

    return fetched


def create_snapshot(
    cache_dir=DEFAULT_CACHE,
):
    cache_dir = (
        Path(cache_dir)
        .expanduser()
        .resolve()
    )

    source_path = (
        cache_dir
        / "Photos.sqlite"
    )

    snapshot_path = (
        cache_dir
        / "Photos-snapshot.sqlite"
    )

    if not source_path.exists():
        raise PhotosDatabaseError(
            "Photos.sqlite has not been fetched."
        )

    if snapshot_path.exists():
        snapshot_path.unlink()

    source = None
    destination = None

    try:
        # This is our LOCAL copy, never the database
        # on the iPhone. SQLite automatically sees
        # the matching -wal/-shm files beside it.
        source = sqlite3.connect(
            str(source_path)
        )

        source.execute(
            "PRAGMA query_only = ON"
        )

        destination = sqlite3.connect(
            str(snapshot_path)
        )

        source.backup(
            destination
        )

        destination.commit()

    except sqlite3.Error as exc:
        raise PhotosDatabaseError(
            "Unable to create Photos database "
            f"snapshot: {exc}"
        )

    finally:
        if destination is not None:
            destination.close()

        if source is not None:
            source.close()

    if not snapshot_path.exists():
        raise PhotosDatabaseError(
            "Photos database snapshot "
            "was not created."
        )

    return snapshot_path


def prepare_snapshot(
    cache_dir=DEFAULT_CACHE,
):
    fetched = fetch_database_files(
        cache_dir
    )

    snapshot = create_snapshot(
        cache_dir
    )

    return snapshot, fetched


def camera_library_summary(
    snapshot_path,
):
    connection = sqlite3.connect(
        str(snapshot_path)
    )

    try:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS camera_photos,

                SUM(
                    CASE
                        WHEN ZHASADJUSTMENTS = 1
                        THEN 1
                        ELSE 0
                    END
                ) AS adjusted,

                SUM(
                    CASE
                        WHEN ZHASADJUSTMENTS = 0
                             OR ZHASADJUSTMENTS IS NULL
                        THEN 1
                        ELSE 0
                    END
                ) AS unadjusted

            FROM ZGENERICASSET

            WHERE
                ZTRASHEDDATE IS NULL
                AND ZKIND = 0
                AND ZDIRECTORY LIKE 'DCIM/%'
                AND UPPER(ZFILENAME)
                    LIKE '%.JPG'
            """
        ).fetchone()

    except sqlite3.Error as exc:
        raise PhotosDatabaseError(
            "Unable to read Photos database: "
            f"{exc}"
        )

    finally:
        connection.close()

    return {
        "camera_photos": row[0] or 0,
        "adjusted": row[1] or 0,
        "unadjusted": row[2] or 0,
    }