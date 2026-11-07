#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Read-only host snapshot; run over SSH with python3 - (writes no host files)."""

import hashlib
import json
from pathlib import Path
import platform
import subprocess

source = Path("/usr/src/amdgpu-7.1.9-2407772.24.04")
files = [source / name for name in (
    "amd/amdgpu/mes_v12_0.c", "dkms.conf", "amd/dkms/pre-build.sh",
    "amd/dkms/dkms-config.sh", "Makefile",
)]
files += sorted(Path("/lib/modules/6.8.0-147-generic/updates/dkms").glob("*.ko*"))
record = {
    "kernel": platform.release(),
    "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
    "numa_balancing": Path("/proc/sys/kernel/numa_balancing").read_text().strip(),
    "loaded_amdgpu_version": Path("/sys/module/amdgpu/version").read_text().strip(),
    "file_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
    "dkms_status": subprocess.check_output(["dkms", "status"], text=True),
    "packages": subprocess.check_output([
        "dpkg-query", "-W", "amdgpu-dkms", "amdgpu-dkms-firmware", "amdgpu-install",
    ], text=True),
}
print(json.dumps(record, indent=2))
