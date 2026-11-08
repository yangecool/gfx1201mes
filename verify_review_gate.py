#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Verify gate exits and assertions using private userspace source mutations.

The real candidate inputs are read-only; mutations live only in /tmp.
No kernel build, module install/load, SSH, GPU or WB lifecycle test is run.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v1-source", type=Path, required=True)
    parser.add_argument("--v2-source", type=Path, required=True)
    parser.add_argument("--kernel-iopoll", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    harness = Path(__file__).resolve().with_name("review_mes_status.py")
    original = args.v2_source.read_text()
    rows = []

    def run_case(name, source, wanted_exit, required_check=None, compile_error=False):
        folder = output / name
        command = [sys.executable, str(harness), "--candidate-source", str(source),
                   "--kernel-iopoll", str(args.kernel_iopoll), "--output-dir", str(folder)]
        process = subprocess.run(command, text=True, capture_output=True, timeout=30)
        report = json.loads((folder / "results.json").read_text())
        row = {"name": name, "exit": process.returncode, "expected_exit": wanted_exit,
               "stdout": process.stdout, "stderr": process.stderr,
               "required_failed_check": required_check}
        assert process.returncode == wanted_exit, row
        assert report["process_exit_code"] == wanted_exit, row
        assert report["production_deployment_allowed"] is False, row
        if compile_error:
            assert report["infrastructure_error"]["stage"] == "compile", row
        else:
            assert report["program_exit_code"] == wanted_exit, row
            assert report["direct_case_count"] == 15, row
            assert len(report["ownership_counterexamples"]) == 1, row
            assert report["ownership_counterexamples"][0]["wb_lifecycle_exercised"] is False, row
            if wanted_exit == 0:
                assert report["direct_case_failures"] == 0, row
                assert all(case["pass"] for case in report["direct_cases"]), row
                assert {case["log_variant"] for case in report["direct_cases"]
                        if case["expected"] != 0} == {0, 1, 2}, row
            else:
                assert report["direct_case_failures"] > 0, row
            if required_check:
                assert any(required_check in case["failed_checks"]
                           for case in report["direct_cases"]), row
        row["pass"] = True
        rows.append(row)
        print(json.dumps({"case": name, "exit": process.returncode, "pass": True}), flush=True)

    run_case("v1", args.v1_source.resolve(), 1, "remaining_and_100us_budget")
    run_case("v2", args.v2_source.resolve(), 0)
    mutations = [
        ("nonzero_is_success", "if (r < 1 || status != 1)",
         "if (r < 1 || !lower_32_bits(status))", "return_code"),
        ("remove_100us_limit", "min_t(signed long, r, 100)", "r",
         "remaining_and_100us_budget"),
        ("delay_immediate_path", "status = READ_ONCE(*status_ptr);",
         "udelay(1);\n\tstatus = READ_ONCE(*status_ptr);",
         "immediate_path_has_one_read_and_no_delay"),
        ("extra_status_read", "status = READ_ONCE(*status_ptr);",
         "status = READ_ONCE(*status_ptr);\n\t(void)READ_ONCE(*status_ptr);", "status_read_limit"),
        ("wrong_snapshot_in_log", "upper_32_bits(status), lower_32_bits(status)",
         "upper_32_bits(0ULL), lower_32_bits(0ULL)",
         "error_log_matches_expected_snapshot_and_fence"),
        ("wrong_fence_in_log", "pipe, op_str, misc_op_str, r,",
         "pipe, op_str, misc_op_str, r - 1,", "error_log_matches_expected_snapshot_and_fence"),
        ("duplicate_free", "amdgpu_wb_free(adev, status_offset);",
         "amdgpu_wb_free(adev, status_offset);\n\tamdgpu_wb_free(adev, status_offset);",
         "exactly_one_wb_free_call"),
        ("poll_without_fence_budget", "if (r > 0 && !status)", "if (!status)",
         "immediate_path_has_one_read_and_no_delay"),
    ]
    with tempfile.TemporaryDirectory(prefix="mesmock-gate-mutations-", dir="/tmp") as scratch:
        for name, before, after, required_check in mutations:
            assert before in original, name
            changed = original.replace(before, after)
            source = Path(scratch) / f"{name}.c"
            source.write_text(changed)
            run_case(name, source, 1, required_check)
        broken = Path(scratch) / "broken.c"
        broken.write_text(original.replace("status != 1", "status != )"))
        run_case("compile_failure", broken, 2, compile_error=True)

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    report = {
        "passed": len(rows), "failed": 0, "runs": rows,
        "source_sha256": {str(p): digest(p) for p in (args.v1_source, args.v2_source)},
        "harness_sha256": digest(harness), "verifier_sha256": digest(Path(__file__)),
        "kernel_iopoll_sha256": digest(args.kernel_iopoll),
        "driver_patch_changed": False, "hardware_access": False,
        "wb_lifecycle_exercised": False, "production_deployment_allowed": False,
    }
    (output / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": len(rows), "failed": 0,
                      "verification": str(output / "verification.json")}))


if __name__ == "__main__":
    main()
