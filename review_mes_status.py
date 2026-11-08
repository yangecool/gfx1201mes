#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Adversarial review of the actual MES status/cleanup block, in userspace.

No devices, privileged operations, or installed driver files are modified.
Exit 0 means the 15 direct return/behavior cases passed; exit 1 means a direct
assertion failed; exit 2 means the test could not be executed reliably.
The separate ownership example only feeds a value sequence. It exercises no
A/B allocation, submission, timeout release, or WB-slot reuse and is never
counted as a passed or failed lifecycle test. Production approval stays false.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

import mock_mes_status as base


CASES = r'''
struct review_case {
    const char *name;
    const u64 *sequence;
    size_t count;
    signed long fence;
    int expected;
    unsigned long max_delay_us;
    size_t max_reads;
    u64 expected_snapshot;
    int variant;
};
#define LENGTH(a) (sizeof(a) / sizeof((a)[0]))
#define CASE(n, s, f, e, d, reads, snapshot, variant) \
    {n, s, LENGTH(s), f, e, d, reads, snapshot, variant}
static const u64 ok[] = {1};
static const u64 late_ok[] = {0, 0, 1};
static const u64 fail[] = {UINT64_C(0x8100000e00000000), 1};
static const u64 late_fail[] = {0, 0, UINT64_C(0x8100000e00000000), 1};
static const u64 mixed[] = {UINT64_C(0x8100000e00000001)};
static const u64 late_mixed[] = {0, UINT64_C(0x8100000e00000001)};
static const u64 malformed[] = {2};
static const u64 late_malformed[] = {0, 2};
static const u64 debug[] = {UINT64_C(0x1234567800000001)};
static const u64 pending[] = {0};
static const u64 old_success_then_current_error[] = {
    0, 0, 1, UINT64_C(0x8100000e00000000)
};
static u64 beyond_grace[150];
static const struct review_case tests[] = {
    CASE("normal_success", ok, 2100000, 0, 0, 1, 1, 0),
    CASE("observed_late_success", late_ok, 2100000, 0, 1, 3, 1, 0),
    CASE("firmware_error_latched", fail, 2100000, -ETIMEDOUT,
         0, 1, UINT64_C(0x8100000e00000000), 0),
    CASE("late_firmware_error_latched", late_fail, 2100000, -ETIMEDOUT,
         1, 3, UINT64_C(0x8100000e00000000), 0),
    CASE("error_bit_with_low_one", mixed, 2100000, -ETIMEDOUT,
         0, 1, UINT64_C(0x8100000e00000001), 0),
    CASE("late_error_bit_with_low_one", late_mixed, 2100000, -ETIMEDOUT,
         0, 2, UINT64_C(0x8100000e00000001), 0),
    CASE("unexpected_low_two", malformed, 2100000, -ETIMEDOUT, 0, 1, 2, 1),
    CASE("late_unexpected_low_two", late_malformed, 2100000, -ETIMEDOUT, 0, 2, 2, 1),
    CASE("unexpected_high_bits_with_low_one", debug, 2100000, -ETIMEDOUT,
         0, 1, UINT64_C(0x1234567800000001), 2),
    CASE("permanently_zero_full_budget", pending, 2100000, -ETIMEDOUT, 100, 103, 0, 0),
    CASE("permanently_zero_short_budget", pending, 3, -ETIMEDOUT, 3, 6, 0, 0),
    CASE("zero_fence_budget", late_ok, 0, -ETIMEDOUT, 0, 1, 0, 0),
    CASE("negative_fence_budget", ok, -5, -ETIMEDOUT, 0, 1, 1, 0),
    CASE("one_us_remaining", late_ok, 1, 0, 1, 3, 1, 0),
    CASE("completion_beyond_100us_grace", beyond_grace, 2100000,
         -ETIMEDOUT, 100, 103, 0, 0),
};

static const char *failed_checks[16];
static unsigned int failed_count, checks_run;
static void expect(bool condition, const char *label)
{
    ++checks_run;
    if (!condition) {
        assert(failed_count < LENGTH(failed_checks));
        failed_checks[failed_count++] = label;
    }
}
static void reset(const u64 *sequence, size_t count, signed long fence, int variant)
{
    values = sequence;
    value_count = count;
    fence_result = fence;
    reads = delays = frees = log_count = delayed_us = 0;
    last_status = 0;
    last_log[0] = '\0';
    log_variant = variant;
    failed_count = 0;
}
int main(void)
{
    beyond_grace[149] = 1;
    unsigned int direct_failures = 0, return_failures = 0, assertion_failures = 0;
    printf("{\"schema_version\":2,\"direct_cases\":[");
    for (size_t i = 0; i < LENGTH(tests); ++i) {
        const struct review_case *test = &tests[i];
        reset(test->sequence, test->count, test->fence, test->variant);
        int result = reviewed_decision();
        unsigned long budget = test->fence > 0 ? (unsigned long)test->fence : 0;
        if (budget > 100)
            budget = 100;
        expect(result == test->expected, "return_code");
        return_failures += result != test->expected;
        expect(frees == 1, "exactly_one_wb_free_call");
        expect(reads >= 1 && reads <= test->max_reads, "status_read_limit");
        expect(last_status == test->expected_snapshot, "terminal_snapshot");
        expect(delayed_us <= budget, "remaining_and_100us_budget");
        expect(delays <= budget, "delay_call_budget");
        expect(delayed_us <= test->max_delay_us, "case_delay_limit");
        if (test->max_reads == 1)
            expect(reads == 1 && delays == 0 && delayed_us == 0,
                   "immediate_path_has_one_read_and_no_delay");
        if (test->expected == 0) {
            expect(log_count == 0 && last_log[0] == '\0', "success_has_no_error_log");
        } else {
            char expected_log[512];
            const char *format = test->variant == 0 ?
                "MES(1) failed to respond to msg=MISC (INV_TLBS) fence_wait_ret=%ld status=0x%08x_%08x\n" :
                test->variant == 1 ?
                "MES(1) failed to respond to msg=MISC fence_wait_ret=%ld status=0x%08x_%08x\n" :
                "MES(1) failed to respond to msg=14 fence_wait_ret=%ld status=0x%08x_%08x\n";
            snprintf(expected_log, sizeof(expected_log), format, test->fence,
                     upper_32_bits(test->expected_snapshot), lower_32_bits(test->expected_snapshot));
            expect(log_count == 1, "failure_has_exactly_one_error_log");
            expect(strcmp(last_log, expected_log) == 0, "error_log_matches_expected_snapshot_and_fence");
        }
        direct_failures += failed_count != 0;
        assertion_failures += failed_count;
        printf("%s{\"name\":\"%s\",\"expected\":%d,\"actual\":%d,"
               "\"reads\":%zu,\"max_reads\":%zu,\"nominal_delay_us\":%lu,"
               "\"remaining_budget_limit_us\":%lu,\"case_delay_limit_us\":%lu,"
               "\"free_calls\":%u,\"error_log_count\":%u,\"log_variant\":%d,"
               "\"pass\":%s,\"failed_checks\":[", i ? "," : "", test->name,
               test->expected, result, reads, test->max_reads, delayed_us, budget,
               test->max_delay_us, frees, log_count, test->variant,
               failed_count ? "false" : "true");
        for (unsigned int n = 0; n < failed_count; ++n)
            printf("%s\"%s\"", n ? "," : "", failed_checks[n]);
        printf("]}");
    }
    reset(old_success_then_current_error, LENGTH(old_success_then_current_error), 2100000, 0);
    int ownership_result = reviewed_decision();
    printf("],\"direct_case_count\":%zu,\"direct_case_failures\":%u,"
           "\"return_policy_case_failures\":%u,\"direct_assertion_failures\":%u,"
           "\"direct_assertions_checked\":%u,\"direct_gate_passed\":%s,"
           "\"ownership_counterexamples\":[{\"name\":\"conditional_stale_success_from_old_request\","
           "\"evidence_kind\":\"value_sequence_only\",\"observed_rc\":%d,"
           "\"reads\":%zu,\"wb_lifecycle_exercised\":false,\"status\":\"unresolved\"}],"
           "\"production_deployment_allowed\":false,\"hardware_access\":false}\n",
           LENGTH(tests), direct_failures, return_failures, assertion_failures,
           checks_run, direct_failures ? "false" : "true", ownership_result, reads);
    return direct_failures ? EXIT_FAILURE : EXIT_SUCCESS;
}
'''


def generate(source, iopoll):
    preamble = base.PREAMBLE.replace("<= 1000", "<= 2100001")
    preamble += "\n#define min_t(type, a, b) ((type)(a) < (type)(b) ? (type)(a) : (type)(b))\n"
    notices = source[:source.index("*/") + 2] + "\n"
    notices += iopoll[:iopoll.index("#ifndef _LINUX_IOPOLL_H")]
    wrapper = base.WRAPPER.replace("@NAME@", "reviewed_decision")
    wrapper = wrapper.replace("@BLOCK@", base.extract_block(source))
    return preamble + notices + base.extract_poll_macro(iopoll) + wrapper + CASES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-source", type=Path, required=True)
    parser.add_argument("--kernel-iopoll", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    code = generate(args.candidate_source.read_text(), args.kernel_iopoll.read_text())
    c_file = output / "review_mock.c"
    c_file.write_text(code)
    command = ["gcc-13", "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror",
               "-fsanitize=address,undefined", "-fno-omit-frame-pointer", str(c_file),
               "-o", str(output / "review_mock")]
    def infrastructure_failure(stage, message):
        report = {"schema_version": 2, "direct_gate_passed": False,
                  "infrastructure_error": {"stage": stage, "message": message},
                  "compiler_command": command, "process_exit_code": 2,
                  "production_deployment_allowed": False, "hardware_access": False}
        (output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))
        raise SystemExit(2)

    compiled = subprocess.run(command, text=True, capture_output=True)
    (output / "compile.log").write_text(compiled.stdout + compiled.stderr)
    if compiled.returncode:
        infrastructure_failure("compile", compiled.stderr)
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0", UBSAN_OPTIONS="halt_on_error=1")
    try:
        run = subprocess.run([str(output / "review_mock")], text=True, capture_output=True,
                             timeout=15, env=env)
    except subprocess.TimeoutExpired:
        infrastructure_failure("run", "15-second userspace test timeout")
    (output / "run.stdout").write_text(run.stdout)
    (output / "run.stderr").write_text(run.stderr)
    if run.returncode not in (0, 1):
        infrastructure_failure("run", f"program exit {run.returncode}: {run.stderr}")
    try:
        report = json.loads(run.stdout)
    except json.JSONDecodeError:
        infrastructure_failure("parse", "program did not emit a JSON report")
    expected_exit = 1 if report["direct_case_failures"] else 0
    if run.returncode != expected_exit:
        infrastructure_failure("gate", "program exit disagrees with its direct assertion failures")
    report["source_sha256"] = hashlib.sha256(args.candidate_source.read_bytes()).hexdigest()
    report["compiler_command"] = command
    report["program_exit_code"] = run.returncode
    report["process_exit_code"] = expected_exit
    report["gate_scope"] = "direct return/behavior assertions only; not production approval"
    report["harness_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report["kernel_iopoll_sha256"] = hashlib.sha256(args.kernel_iopoll.read_bytes()).hexdigest()
    (output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"direct_case_failures": report["direct_case_failures"],
                      "direct_gate_passed": report["direct_gate_passed"],
                      "ownership_counterexamples_unresolved": len(report["ownership_counterexamples"]),
                      "process_exit_code": expected_exit,
                      "results": str(output / "results.json")}))
    raise SystemExit(expected_exit)


if __name__ == "__main__":
    main()
