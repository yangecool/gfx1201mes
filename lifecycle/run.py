#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run CPU-only allocation, submission and deferred firmware writeback scenarios."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

SOURCES = {
    "amd/amdgpu/amdgpu_wb.c": "40fa170ddd174c021f634f1648011e2f686043db23cd30241de13fd3f0489ec9",
    "amd/amdgpu/amdgpu_fence.c": "2dd7103028ce36fef9a5408db72e4bdc77a2792a2cc6b2e0162cafacd1ed0e19",
    "amd/include/mes_v12_api_def.h": "b41acfb382312e6ff5492ebd48687e9d4fd94d5b7b539f269d99342302bd70db",
}
BASE_SHA = "c32c4ebcdbb9cca62c3464d4043f15c935d986fcc30e6a4600e9cbbfb119ac7b"
POLL_SHA = "ae14ea5396f486ab5be2c364fe9ce10226c7088dbc1d798c6a6d8b88854b1558"


def checked(path, digest):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"input does not match the 4U source: {path}")
    return data.decode()


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index("\n}", start) + 2]


def run(args, work):
    source = {name: checked(args.dkms / name, sha) for name, sha in SOURCES.items()}
    poll = checked(args.iopoll, POLL_SHA)
    base = checked(args.original, BASE_SHA)
    candidate = args.candidate.read_text()
    macro = poll.index("#define read_poll_timeout_atomic(")
    end = next(i for i, line in enumerate(poll[macro:].splitlines(True))
               if not line.rstrip().endswith("\\"))
    signature = "static int mes_v12_0_submit_pkt_and_poll_completion("
    wb = source["amd/amdgpu/amdgpu_wb.c"]
    fence = source["amd/amdgpu/amdgpu_fence.c"]
    include = [
        poll[:poll.index("#ifndef _LINUX_IOPOLL_H")],
        "".join(poll[macro:].splitlines(True)[:end + 1]),
        wb[:wb.index("#include")], function(wb, "int amdgpu_wb_get("),
        function(wb, "void amdgpu_wb_free("),
        fence[:fence.index("*/") + 2], function(fence, "signed long amdgpu_fence_wait_polling("),
        base[:base.index("*/") + 2],
        function(base, signature).replace(signature, "static int stock_submit(", 1),
    ]
    private = "struct mes_v12_inv_wb {" in candidate
    if private:
        include.append(candidate[candidate.index("#define MES_INV_WB_STRIDE"):candidate.index(signature)])
        include += [function(candidate, f"static void mes_v12_inv_wb_{phase}(") for phase in ("init", "fini")]
    include.append(function(candidate, signature).replace(signature, "static int candidate_submit(", 1))
    (work / "extracted.inc").write_text("\n".join(include))
    binary = work / "ownership"
    cmd = ["gcc-13", "-std=gnu11", "-O2", "-fno-strict-aliasing", "-Wall", "-Wextra", "-Werror",
           "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-no-pie", "-pthread",
           f"-DHAS_PRIVATE_WB={int(private)}", "-I", str(work), "-I", str(args.dkms / "amd/include"),
           str(Path(__file__).with_name("ownership.c")), "-o", str(binary)]
    subprocess.run(cmd, check=True)
    lines = subprocess.check_output([binary], text=True, timeout=60).splitlines()
    if private:
        subprocess.run(cmd[:1] + ["-DMODEL_SLOTS=1024", "-DFULL_CAPACITY_ONLY"] + cmd[1:], check=True)
        lines.extend(subprocess.check_output([binary], text=True, timeout=60).splitlines())
    cases = [json.loads(line) for line in lines]
    stock = [row for row in cases if row.get("version") == "installed_source"]
    revised = [row for row in cases if row.get("version") == "candidate"]
    if len(stock) != 5 or len(revised) != 5:
        raise ValueError("incomplete A/B run")
    for row in stock:
        expected = 0 if row["schedule"] == "B_own_immediate_success" else -110
        if row["B_rc"] != expected:
            raise ValueError(f"unexpected baseline control: {row}")
    failures = [row["schedule"] for row in revised if row["B_rc"] !=
                (0 if row["schedule"] in {"B_own_late_success", "B_own_immediate_success"} else -110)]
    failures.extend(f'{row["check"]} ({row["mock_pool_slots"]} slots)' for row in cases if row.get("pass") is False)
    result = {
        "hardware_access": False,
        "firmware_ordering": "scheduled CPU fault injection, not an observed hardware interleaving",
        "original_sha256": BASE_SHA, "candidate_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
        "sources_sha256": {**SOURCES, "include/linux/iopoll.h": POLL_SHA},
        "cases": cases, "failed_candidate_cases": failures,
        "exit_code": int(bool(failures)),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"{args.output}: {len(cases)} results, candidate failures {failures}", flush=True)
    return result["exit_code"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["dkms", "iopoll", "original", "candidate", "output"]:
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="mes-ownership-") as temp:
        return run(args, Path(temp))


if __name__ == "__main__":
    raise SystemExit(main())
