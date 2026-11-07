#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile the AMD package against a private copy of 4U's headers; never install.

The input trees must already be extracted below a dedicated /tmp directory.
Only the private build scripts are adjusted for relocated headers/Ubuntu 24.04.
The functional MES change is supplied separately as a patch.
"""

import argparse
import difflib
import json
import os
from pathlib import Path
import subprocess
import time


def run_logged(command, cwd, output):
    start = time.monotonic()
    with output.open("w") as log:
        result = subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
    record = {
        "command": command,
        "cwd": str(cwd),
        "returncode": result.returncode,
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "log": str(output),
    }
    output.with_suffix(".json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record), flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--phase", choices=("prepare", "baseline", "candidate"), required=True)
    parser.add_argument("--jobs", type=int, default=8)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if root.parent != Path("/tmp") or not root.name.startswith("cks4u-mes-build-"):
        parser.error("refusing a build outside a dedicated /tmp/cks4u-mes-build-* tree")
    if not 1 <= args.jobs <= 16:
        parser.error("jobs must be in 1..16")
    source = root / "package/usr/src/amdgpu-7.1.9-2407772.24.04"
    headers = root / "headers/linux-headers-6.8.0-147-generic"
    for path in (source, headers):
        if not path.is_dir() or not path.resolve().is_relative_to(root):
            parser.error(f"missing or non-private tree: {path}")
    os.environ["AMDGPU_BTF"] = "0"
    os.environ["LC_ALL"] = "C"
    os.environ.pop("MAKEFLAGS", None)

    if args.phase == "prepare":
        stamp = root / "prepared.json"
        started = root / "prepare-started.json"
        if stamp.exists() or started.exists():
            parser.error("preparation already attempted; use a fresh extracted tree instead of preprocessing twice")
        started.write_text(json.dumps({"source": str(source)}, indent=2) + "\n")
        edits = []
        for relative, old, new in (
            ("amd/dkms/pre-build.sh", "(cd $SRC && ./configure CC=${CC})",
             f'(cd $SRC && ./configure CC=${{CC}} --with-linux="{headers}" --with-linux-obj="{headers}")'),
            ("amd/dkms/dkms-config.sh", 'kernel_build_dir="/lib/modules/$KERNELVER/build"',
             f'kernel_build_dir="{headers}"'),
            ("amd/dkms/dkms-config.sh", 'OS_NAME=$(grep -oP \'(?<=^ID=).+\' /etc/os-release | tr -d \'"\')',
             'OS_NAME="ubuntu"'),
            ("amd/dkms/dkms-config.sh", 'OS_VERSION=$(grep -oP \'(?<=^VERSION_ID=).+\' /etc/os-release | tr -d \'"\')',
             'OS_VERSION="24.04"'),
        ):
            target = source / relative
            before = target.read_text()
            if before.count(old) != 1:
                raise RuntimeError(f"expected exactly one build-only replacement in {relative}: {old}")
            after = before.replace(old, new)
            edits.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                              fromfile=f"a/{relative}", tofile=f"b/{relative}"))
            target.write_text(after)
        (root / "build-isolation-adjustments.patch").write_text("".join(edits))
        command = [
            "make", "sanity-check", "amd/dkms/dkms-config.mk",
            "KERNELVER=6.8.0-147-generic", f"kernel_build_dir={headers}",
            f"module_build_dir={root / 'module-source-link'}", "CC=gcc-13",
        ]
        run_logged(command, source, root / "prepare.log")
        stamp.write_text(json.dumps({"source": str(source), "headers": str(headers)}, indent=2) + "\n")
        return

    if not (root / "prepared.json").is_file():
        parser.error("run the prepare phase first")
    command = [
        "make", "-C", str(headers), f"M={source}", f"-j{args.jobs}",
        "CC=gcc-13", "TTM_NAME=amdttm", "SCHED_NAME=amd-sched",
        "modules",
    ]
    run_logged(command, source, root / f"{args.phase}-build.log")


if __name__ == "__main__":
    main()
