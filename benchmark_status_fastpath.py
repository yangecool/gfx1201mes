#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""CPU-only comparison of extracted normal-success decision blocks.

This is not a GPU/driver throughput benchmark. Fence waiting, WB freeing and
logging are stubs; a volatile success word prevents constant folding. The
real poll macro is included, but entering any delay aborts this fast-path test.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import tempfile

import mock_mes_status as base


PREAMBLE = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
typedef uint64_t u64;
typedef int64_t s64;
typedef uint32_t u32;
#define NSEC_PER_USEC 1000
#define lower_32_bits(x) ((u32)(x))
#define upper_32_bits(x) ((u32)((u64)(x) >> 32))
#define min_t(type, a, b) ((type)(a) < (type)(b) ? (type)(a) : (type)(b))
static volatile u64 bench_status = 1;
static volatile signed long fence_result = 2100000;
static volatile unsigned long sink;
static int log_variant, halt_if_hws_hang;
#define READ_ONCE(value) (value)
#define amdgpu_fence_wait_polling(ring, seq, timeout) fence_result
#define udelay(us) abort()
#define cpu_relax() __asm__ __volatile__("pause")
static void bench_log(const char *fmt, ...)
{ __asm__ __volatile__("" : : "r"(fmt) : "memory"); }
#define dev_err(device, ...) bench_log(__VA_ARGS__)
static void amdgpu_wb_free(void *device, u32 offset)
{ __asm__ __volatile__("" : : "r"(device), "r"(offset) : "memory"); }
static void schedule(void) { abort(); }
'''

MEASURE = r'''
static uint64_t now_ns(void)
{
    struct timespec ts;
    assert(clock_gettime(CLOCK_MONOTONIC_RAW, &ts) == 0);
    return (uint64_t)ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}
static double measure(int (*function)(void), unsigned long iterations)
{
    unsigned long total = 0;
    uint64_t begin = now_ns();
    for (unsigned long i = 0; i < iterations; ++i)
        total += (unsigned long)function();
    uint64_t end = now_ns();
    sink = total;
    assert(sink == 0);
    return (double)(end - begin) / iterations;
}
int main(void)
{
    int (*functions[])(void) = {baseline_path, v1_path, v2_path};
    const char *names[] = {"baseline", "v1", "v2"};
    for (unsigned int i = 0; i < 3; ++i)
        (void)measure(functions[i], 100000);
    puts("variant,round,ns_per_call");
    for (unsigned int round = 0; round < 11; ++round)
        for (unsigned int slot = 0; slot < 3; ++slot) {
            unsigned int i = (slot + round) % 3;
            printf("%s,%u,%.6f\n", names[i], round,
                   measure(functions[i], 10000000));
        }
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "v1", "v2"):
        parser.add_argument(f"--{name}-source", type=Path, required=True)
    parser.add_argument("--kernel-iopoll", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    numa = Path("/proc/sys/kernel/numa_balancing")
    if numa.read_text().strip() != "0":
        parser.error("NUMA balancing must already be 0; this tool never changes it")
    cpu = min(os.sched_getaffinity(0))
    os.sched_setaffinity(0, {cpu})
    paths = [args.baseline_source, args.v1_source, args.v2_source]
    texts = [path.read_text() for path in paths]
    header = args.kernel_iopoll.read_text()
    code = PREAMBLE + texts[0][:texts[0].index("*/") + 2] + "\n"
    code += header[:header.index("#ifndef _LINUX_IOPOLL_H")]
    code += base.extract_poll_macro(header)
    for name, source in zip(("baseline", "v1", "v2"), texts):
        wrapper = base.WRAPPER.replace("static int @NAME@(void)",
                    f"static __attribute__((noinline, noclone)) int {name}_path(void)")
        wrapper = wrapper.replace("u64 word = 0;", "")
        wrapper = wrapper.replace("u64 *status_ptr __attribute__((unused)) = &word;",
                                  "volatile u64 *status_ptr = &bench_status;")
        code += wrapper.replace("@BLOCK@", base.extract_block(source))
    code += MEASURE
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    c_file = output / "fastpath_benchmark.c"
    c_file.write_text(code)
    with tempfile.TemporaryDirectory(prefix="mesmock-cpu-bench-", dir="/tmp") as scratch:
        binary = Path(scratch) / "bench"
        command = ["gcc-13", "-std=gnu11", "-O2", "-Wall", "-Wextra", "-Werror",
                   str(c_file), "-o", str(binary)]
        compiled = subprocess.run(command, text=True, capture_output=True)
        (output / "compile.log").write_text(compiled.stdout + compiled.stderr)
        if compiled.returncode:
            raise SystemExit(compiled.stderr)
        run = subprocess.run([str(binary)], text=True, capture_output=True,
                             timeout=30, check=True)
        asm = subprocess.check_output(["objdump", "-d", "-M", "intel", str(binary)], text=True)
    assert numa.read_text().strip() == "0"
    (output / "measurements.csv").write_text(run.stdout)
    (output / "disassembly.txt").write_text(asm)
    values = {name: [] for name in ("baseline", "v1", "v2")}
    for line in run.stdout.splitlines()[1:]:
        name, _, ns = line.split(",")
        values[name].append(float(ns))
    medians = {name: statistics.median(samples) for name, samples in values.items()}
    report = {
        "kind": "CPU-only extracted fast-path surrogate, not GPU performance acceptance",
        "median_ns_per_call": medians,
        "v2_minus_baseline_ns": medians["v2"] - medians["baseline"],
        "samples": values, "iterations_per_sample": 10000000,
        "cpu_affinity": [cpu], "numa_balancing_before_and_after": 0,
        "host_kernel": platform.release(), "hardware_access": False,
        "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        "limitations": "No GPU, real WB allocator, fence waiting, DMA ordering or model work. Nanosecond differences are sensitive to code layout and scheduling; not token-throughput evidence.",
    }
    (output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"medians_ns": medians, "results": str(output / "results.json")}))


if __name__ == "__main__":
    main()
