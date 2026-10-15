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


console = Console()


def status_command():
    try:
        with console.status(
            "[bold]Looking for iPhone 4S..."
        ):
            device = require_supported_device()

    except UnsupportedDeviceError as exc:
        console.print()
        console.print(
            Panel(
                str(exc),
                title="LegacyPhotos",
                border_style="red",
            )
        )

        return 1

    except DeviceError as exc:
        console.print()
        console.print(
            f"[red]LegacyPhotos:[/red] {exc}"
        )

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
        help="Detect and verify the connected iPhone 4S.",
    )

    args = parser.parse_args()

    if args.command == "status":
        return status_command()

    return 1


if __name__ == "__main__":
    sys.exit(main())