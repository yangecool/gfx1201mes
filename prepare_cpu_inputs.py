#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Prepare independent, source-pinned MES candidates for CPU tests.

The input directory is read-only. Only the two files needed to apply the
archived patches are copied into a new output directory. No driver build,
installation, SSH connection or device access is performed.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


REPO = Path(__file__).resolve().parent
PINNED = {
    "mes_v12_0.c": "c32c4ebcdbb9cca62c3464d4043f15c935d986fcc30e6a4600e9cbbfb119ac7b",
    "amdgpu_mes.h": "b517f3df875d38a4c35602099bb8b2eb2fc799651cd29e061fc00f51a6881c85",
}
PATCHES = {
    "v1": "patches/0001-mes12-confirm-api-status-after-fence.patch",
    "v2": "patches/review-v2/0001-mes12-bound-and-validate-api-status.patch",
    "v3": "patches/review-v3/0001-mes12-isolate-invalidate-completions.patch",
    "protocol": "patches/protocol-probe/mes-protocol-trial.patch",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dkms-source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    source = args.dkms_source.resolve(strict=True) / "amd/amdgpu"
    for name, expected in PINNED.items():
        if digest(source / name) != expected:
            parser.error(f"source does not match the investigated AMD package: {source / name}")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    record = {"original_sha256": PINNED, "candidates": {}}
    for version, relative in PATCHES.items():
        root = output / version
        target = root / "amd/amdgpu"
        target.mkdir(parents=True)
        for name in PINNED:
            shutil.copyfile(source / name, target / name)
        patch = REPO / relative
        command = ["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-i", str(patch)]
        subprocess.run(command + ["--dry-run"], cwd=root, check=True)
        subprocess.run(command, cwd=root, check=True)
        record["candidates"][version] = {
            "patch": relative,
            "patch_sha256": digest(patch),
            "source": str(target / "mes_v12_0.c"),
            "source_sha256": digest(target / "mes_v12_0.c"),
        }
    (output / "inputs.json").write_text(json.dumps(record, indent=2) + "\n")
    print(output / "inputs.json")


if __name__ == "__main__":
    main()
