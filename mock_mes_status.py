#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile and run the real original/patched MES decision blocks in userspace.

Only GPU memory reads, fence return, delays, logging, and WB free are mocked.
The poll macro is copied from the specified target kernel header. No devices,
kernel modules, privileged operations, or real waits are used.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def extract_block(text):
    marker = "\tr = amdgpu_fence_wait_polling(ring, seq, timeout);"
    start = text.rindex(marker)
    end = text.index("\nerror_undo:", start)
    cleanup = text.index("\nerror_wb_free:", end)
    cleanup_end = text.index("\n}", cleanup)
    return text[start:end] + text[cleanup:cleanup_end]


def extract_poll_macro(text):
    start = text.index("#define read_poll_timeout_atomic(")
    result = []
    for line in text[start:].splitlines(True):
        result.append(line)
        if not line.rstrip().endswith("\\"):
            break
    return "".join(result)


PREAMBLE = r'''/* SPDX-License-Identifier: GPL-2.0-only
 * Generated from MIT AMD driver blocks and the GPL target-kernel poll macro.
 * This executable has no driver loading or hardware access functionality.
 */
#include <assert.h>
#include <errno.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint64_t u64;
typedef int64_t s64;
typedef uint32_t u32;
#define NSEC_PER_USEC 1000
#define lower_32_bits(x) ((u32)(x))
#define upper_32_bits(x) ((u32)((u64)(x) >> 32))
static const u64 *values;
static size_t value_count, reads;
static unsigned int delays, frees, log_count;
static unsigned long delayed_us;
static signed long fence_result;
static u64 last_status;
static int log_variant, halt_if_hws_hang;
static char last_log[512];
static u64 mock_read_status(void)
{
    size_t index = reads++;
    if (index >= value_count)
        index = value_count - 1;
    last_status = values[index];
    return last_status;
}
#define READ_ONCE(value) mock_read_status()
#define amdgpu_fence_wait_polling(ring, seq, timeout) fence_result
static void mock_delay(unsigned long us)
{
    assert(us == 1);
    assert(++delays <= 1000); /* Fail rather than hang on an unbounded poll. */
    delayed_us += us;
}
#define udelay(us) mock_delay(us)
#define cpu_relax() ((void)0)
static void mock_log(const char *format, ...) __attribute__((format(printf, 1, 2)));
static void mock_log(const char *format, ...)
{
    va_list args;
    va_start(args, format);
    vsnprintf(last_log, sizeof(last_log), format, args);
    va_end(args);
    ++log_count;
}
#define dev_err(device, ...) mock_log(__VA_ARGS__)
static void amdgpu_wb_free(void *device, u32 offset)
{
    (void)device;
    assert(offset == 0);
    ++frees;
}
static void schedule(void) { abort(); }
'''

WRAPPER = r'''
static int @NAME@(void)
{
    u64 word = 0;
    u64 *status_ptr __attribute__((unused)) = &word;
    u64 status __attribute__((unused));
    signed long r;
    void *adev = NULL;
    u32 status_offset = 0;
    int pipe = 1;
    const char *op_str = log_variant == 2 ? NULL : "MISC";
    const char *misc_op_str = log_variant ? NULL : "INV_TLBS";
    struct { struct { int opcode; } header; } packet = { { 14 } };
    __typeof__(packet) *x_pkt = &packet;
@BLOCK@
}
'''

TESTS = r'''
struct test_case {
    const char *name;
    const u64 *sequence;
    size_t count;
    signed long fence;
    int expected_original, expected_candidate, variant;
    unsigned int max_delay;
    bool immediate;
};
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define CASE(name, seq, fence, before, after, variant, delay, immediate) \
    {name, seq, ARRAY_SIZE(seq), fence, before, after, variant, delay, immediate}
static const u64 success[] = {1};
static const u64 late_success[] = {0, 1};
static const u64 delayed_success[] = {0, 0, 1};
static const u64 fw_error[] = {UINT64_C(0x8100000e00000000), 1};
static const u64 late_error[] = {0, 0, UINT64_C(0x8100000e00000000), 1};
static const u64 debug_only[] = {UINT64_C(0x1234567800000000), 1};
static const u64 success_debug[] = {UINT64_C(0x1234567800000001)};
static const u64 nonzero_low[] = {2};
static const u64 pending[] = {0};
static u64 success_after_budget[65];
static const struct test_case cases[] = {
    CASE("normal_success", success, 100, 0, 0, 0, 0, true),
    CASE("observed_zero_then_success", late_success, 100, -ETIMEDOUT, 0, 0, 0, false),
    CASE("delayed_success", delayed_success, 100, -ETIMEDOUT, 0, 0, 1, false),
    CASE("firmware_error_not_retried", fw_error, 100, -ETIMEDOUT, -ETIMEDOUT, 0, 0, true),
    CASE("firmware_error_after_zero", late_error, 100, -ETIMEDOUT, -ETIMEDOUT, 0, 1, false),
    CASE("debug_only_not_retried", debug_only, 100, -ETIMEDOUT, -ETIMEDOUT, 0, 0, true),
    CASE("success_with_high_debug", success_debug, 100, 0, 0, 0, 0, true),
    CASE("preserve_existing_nonzero_low_rule", nonzero_low, 100, 0, 0, 0, 0, true),
    CASE("permanently_zero_is_bounded", pending, 3, -ETIMEDOUT, -ETIMEDOUT, 0, 4, false),
    CASE("fence_timeout_even_with_success", success, 0, -ETIMEDOUT, -ETIMEDOUT, 0, 0, true),
    CASE("negative_fence_even_with_success", success, -5, -ETIMEDOUT, -ETIMEDOUT, 0, 0, true),
    CASE("zero_budget_never_unbounded", late_success, 0, -ETIMEDOUT, -ETIMEDOUT, 0, 0, true),
    CASE("negative_budget_never_polled", pending, -5, -ETIMEDOUT, -ETIMEDOUT, 0, 0, true),
    CASE("one_us_remaining", delayed_success, 1, -ETIMEDOUT, 0, 0, 1, false),
    CASE("success_outside_budget_rejected", success_after_budget, 3, -ETIMEDOUT, -ETIMEDOUT, 0, 4, false),
    CASE("error_snapshot_misc_log", fw_error, 100, -ETIMEDOUT, -ETIMEDOUT, 0, 0, true),
    CASE("error_snapshot_opcode_log", fw_error, 100, -ETIMEDOUT, -ETIMEDOUT, 1, 0, true),
    CASE("error_snapshot_numeric_log", fw_error, 100, -ETIMEDOUT, -ETIMEDOUT, 2, 0, true),
};
static void reset(const struct test_case *test)
{
    values = test->sequence;
    value_count = test->count;
    fence_result = test->fence;
    log_variant = test->variant;
    reads = delays = frees = log_count = 0;
    delayed_us = 0;
    last_status = 0;
    last_log[0] = '\0';
}
int main(void)
{
    success_after_budget[64] = 1;
    printf("{\"tests\":[");
    for (size_t i = 0; i < ARRAY_SIZE(cases); ++i) {
        const struct test_case *test = &cases[i];
        reset(test);
        int original_result = original_decision();
        assert(original_result == test->expected_original);
        assert(frees == 1 && delays == 0);
        reset(test);
        int candidate_result = candidate_decision();
        if (candidate_result != test->expected_candidate) {
            fprintf(stderr, "%s: expected %d, got %d\n", test->name,
                    test->expected_candidate, candidate_result);
            return 1;
        }
        assert(frees == 1);
        assert(delayed_us <= test->max_delay);
        if (test->immediate)
            assert(reads == 1 && delays == 0);
        if (candidate_result) {
            char expected[80];
            assert(log_count == 1);
            snprintf(expected, sizeof(expected), "status=0x%08x_%08x",
                     upper_32_bits(last_status), lower_32_bits(last_status));
            assert(strstr(last_log, expected));
            snprintf(expected, sizeof(expected), "fence_wait_ret=%ld", test->fence);
            assert(strstr(last_log, expected));
        } else {
            assert(log_count == 0);
        }
        printf("%s{\"name\":\"%s\",\"original_rc\":%d,\"candidate_rc\":%d,"
               "\"candidate_reads\":%zu,\"delay_calls\":%u,\"nominal_delay_us\":%lu,\"pass\":true}",
               i ? "," : "", test->name, original_result, candidate_result,
               reads, delays, delayed_us);
    }
    printf("],\"passed\":%zu,\"failed\":0,\"hardware_access\":false}\n", ARRAY_SIZE(cases));
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-source", type=Path, required=True)
    parser.add_argument("--candidate-source", type=Path, required=True)
    parser.add_argument("--kernel-iopoll", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cc", default="gcc-13")
    parser.add_argument("--sanitize", action="store_true")
    args = parser.parse_args()
    baseline = args.baseline_source.read_text()
    candidate = args.candidate_source.read_text()
    header = args.kernel_iopoll.read_text()
    # Accessor instrumentation only: the original direct WB read becomes a
    # scheduled mock read; neither original control flow nor conditions change.
    original_block = extract_block(baseline).replace("*status_ptr", "READ_ONCE(*status_ptr)")
    candidate_block = extract_block(candidate)
    driver_notice = baseline[:baseline.index("*/") + 2] + "\n"
    kernel_notice = header[:header.index("#ifndef _LINUX_IOPOLL_H")]
    source = PREAMBLE + driver_notice + kernel_notice + extract_poll_macro(header)
    source += WRAPPER.replace("@NAME@", "original_decision").replace("@BLOCK@", original_block)
    source += WRAPPER.replace("@NAME@", "candidate_decision").replace("@BLOCK@", candidate_block)
    source += TESTS
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    c_path = output / "mes_status_mock.c"
    binary = output / "mes_status_mock"
    c_path.write_text(source)
    command = [args.cc, "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror"]
    if args.sanitize:
        command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
    command += [str(c_path), "-o", str(binary)]
    compile_result = subprocess.run(command, text=True, capture_output=True)
    (output / "compile.log").write_text(compile_result.stdout + compile_result.stderr)
    if compile_result.returncode:
        raise SystemExit(compile_result.stderr)
    env = dict(os.environ)
    env["ASAN_OPTIONS"] = "detect_leaks=0"
    env["UBSAN_OPTIONS"] = "halt_on_error=1"
    result = subprocess.run([str(binary)], text=True, capture_output=True, env=env, timeout=10)
    (output / "run.stderr").write_text(result.stderr)
    (output / "run.stdout").write_text(result.stdout)
    if result.returncode:
        raise SystemExit(f"mock failed ({result.returncode}): {result.stderr}")
    report = json.loads(result.stdout)
    report["compiler_command"] = command
    report["sanitizers"] = ["address", "undefined"] if args.sanitize else []
    report["input_sha256"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (
        args.baseline_source, args.candidate_source, args.kernel_iopoll,
    )}
    (output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "failed": report["failed"],
                      "sanitizers": report["sanitizers"], "results": str(output / "results.json")}))


if __name__ == "__main__":
    main()
