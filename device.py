#!/usr/bin/env python3

import plistlib
import shutil
import subprocess

from dataclasses import dataclass
from typing import List


SUPPORTED_PRODUCT_TYPE = "iPhone4,1"
SUPPORTED_IOS_VERSION = "9.3.6"


class DeviceError(Exception):
    pass


class UnsupportedDeviceError(DeviceError):
    pass


@dataclass
class Device:
    udid: str
    name: str
    product_type: str
    product_version: str
    build_version: str

    @property
    def is_supported(self):
        return (
            self.product_type == SUPPORTED_PRODUCT_TYPE
            and self.product_version == SUPPORTED_IOS_VERSION
        )


def require_tool(name):
    path = shutil.which(name)

    if not path:
        raise DeviceError(
            f"{name} was not found in PATH. "
            "Install libimobiledevice first."
        )

    return path


def connected_udids() -> List[str]:
    idevice_id = require_tool(
        "idevice_id"
    )

    process = subprocess.run(
        [
            idevice_id,
            "-l",
        ],
        capture_output=True,
        text=True,
    )

    if process.returncode != 0:
        raise DeviceError(
            process.stderr.strip()
            or "Unable to detect connected iOS devices."
        )

    return [
        line.strip()
        for line in process.stdout.splitlines()
        if line.strip()
    ]


def read_device(udid):
    ideviceinfo = require_tool(
        "ideviceinfo"
    )

    process = subprocess.run(
        [
            ideviceinfo,
            "-u",
            udid,
            "-x",
        ],
        capture_output=True,
    )

    if process.returncode != 0:
        error = process.stderr.decode(
            "utf-8",
            errors="replace",
        ).strip()

        raise DeviceError(
            error
            or (
                "Unable to read device information. "
                "Unlock the iPhone and make sure "
                "this Mac is trusted."
            )
        )

    try:
        info = plistlib.loads(
            process.stdout
        )

    except Exception as exc:
        raise DeviceError(
            "Unable to parse device information: "
            f"{exc}"
        )

    return Device(
        udid=udid,
        name=info.get(
            "DeviceName",
            "iPhone",
        ),
        product_type=info.get(
            "ProductType",
            "",
        ),
        product_version=info.get(
            "ProductVersion",
            "",
        ),
        build_version=info.get(
            "BuildVersion",
            "",
        ),
    )


def detect_device():
    udids = connected_udids()

    if not udids:
        raise DeviceError(
            "No iPhone detected. "
            "Connect the iPhone 4S over USB, "
            "unlock it, and try again."
        )

    if len(udids) > 1:
        raise DeviceError(
            "More than one iOS device is connected. "
            "LegacyPhotos currently requires exactly "
            "one connected device."
        )

    return read_device(
        udids[0]
    )


def require_supported_device():
    device = detect_device()

    if not device.is_supported:
        raise UnsupportedDeviceError(
            "Unsupported device.\n\n"
            "LegacyPhotos supports only:\n"
            "  iPhone 4S (iPhone4,1)\n"
            "  iOS 9.3.6\n\n"
            "Detected:\n"
            f"  Product: {device.product_type or 'unknown'}\n"
            f"  iOS:     {device.product_version or 'unknown'}"
        )

    return device