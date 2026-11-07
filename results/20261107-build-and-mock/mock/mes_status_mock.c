/* SPDX-License-Identifier: GPL-2.0-only
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
/*
 * Copyright 2023 Advanced Micro Devices, Inc.
 *
 * Permission is hereby granted, free of charge, to any person obtaining a
 * copy of this software and associated documentation files (the "Software"),
 * to deal in the Software without restriction, including without limitation
 * the rights to use, copy, modify, merge, publish, distribute, sublicense,
 * and/or sell copies of the Software, and to permit persons to whom the
 * Software is furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL
 * THE COPYRIGHT HOLDER(S) OR AUTHOR(S) BE LIABLE FOR ANY CLAIM, DAMAGES OR
 * OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
 * ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
 * OTHER DEALINGS IN THE SOFTWARE.
 *
 */
/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Copyright (c) 2012-2014 The Linux Foundation. All rights reserved.
 */

#define read_poll_timeout_atomic(op, val, cond, delay_us, timeout_us, \
					delay_before_read, args...) \
({ \
	u64 __timeout_us = (timeout_us); \
	s64 __left_ns = __timeout_us * NSEC_PER_USEC; \
	unsigned long __delay_us = (delay_us); \
	u64 __delay_ns = __delay_us * NSEC_PER_USEC; \
	if (delay_before_read && __delay_us) { \
		udelay(__delay_us); \
		if (__timeout_us) \
			__left_ns -= __delay_ns; \
	} \
	for (;;) { \
		(val) = op(args); \
		if (cond) \
			break; \
		if (__timeout_us && __left_ns < 0) { \
			(val) = op(args); \
			break; \
		} \
		if (__delay_us) { \
			udelay(__delay_us); \
			if (__timeout_us) \
				__left_ns -= __delay_ns; \
		} \
		cpu_relax(); \
		if (__timeout_us) \
			__left_ns--; \
	} \
	(cond) ? 0 : -ETIMEDOUT; \
})

static int original_decision(void)
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
	r = amdgpu_fence_wait_polling(ring, seq, timeout);

	/*
	 * status_ptr[31:0] == 0 (fail) or status_ptr[63:0] == 1 (success).
	 * If status_ptr[31:0] == 0 then status_ptr[63:32] will have debug error information.
	 */
	if (r < 1 || !(lower_32_bits(READ_ONCE(*status_ptr)))) {

		if (misc_op_str)
			dev_err(adev->dev, "MES(%d) failed to respond to msg=%s (%s)\n",
				pipe, op_str, misc_op_str);
		else if (op_str)
			dev_err(adev->dev, "MES(%d) failed to respond to msg=%s\n",
				pipe, op_str);
		else
			dev_err(adev->dev, "MES(%d) failed to respond to msg=%d\n",
				pipe, x_pkt->header.opcode);

		while (halt_if_hws_hang)
			schedule();

		r = -ETIMEDOUT;
		goto error_wb_free;
	}

	amdgpu_wb_free(adev, status_offset);
	return 0;

error_wb_free:
	amdgpu_wb_free(adev, status_offset);
	return r;
}

static int candidate_decision(void)
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
	r = amdgpu_fence_wait_polling(ring, seq, timeout);

	/*
	 * A completed scheduler fence can be observed before the API status
	 * writeback. If the status is still unwritten, use only the remaining
	 * fence timeout budget to wait for it. Stop on any nonzero status so
	 * firmware error/debug information is not treated as pending work.
	 * READ_ONCE provides a CPU snapshot, not a firmware ordering guarantee.
	 */
	status = READ_ONCE(*status_ptr);
	if (r > 0 && !status)
		read_poll_timeout_atomic(READ_ONCE, status, status, 1, r, false,
					 *status_ptr);

	/* Low 32 bits indicate success; high 32 bits may carry error details. */
	if (r < 1 || !lower_32_bits(status)) {

		if (misc_op_str)
			dev_err(adev->dev,
				"MES(%d) failed to respond to msg=%s (%s) fence_wait_ret=%ld status=0x%08x_%08x\n",
				pipe, op_str, misc_op_str, r,
				upper_32_bits(status), lower_32_bits(status));
		else if (op_str)
			dev_err(adev->dev,
				"MES(%d) failed to respond to msg=%s fence_wait_ret=%ld status=0x%08x_%08x\n",
				pipe, op_str, r,
				upper_32_bits(status), lower_32_bits(status));
		else
			dev_err(adev->dev,
				"MES(%d) failed to respond to msg=%d fence_wait_ret=%ld status=0x%08x_%08x\n",
				pipe, x_pkt->header.opcode, r,
				upper_32_bits(status), lower_32_bits(status));

		while (halt_if_hws_hang)
			schedule();

		r = -ETIMEDOUT;
		goto error_wb_free;
	}

	amdgpu_wb_free(adev, status_offset);
	return 0;

error_wb_free:
	amdgpu_wb_free(adev, status_offset);
	return r;
}

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
