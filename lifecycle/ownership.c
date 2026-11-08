/* SPDX-License-Identifier: GPL-2.0-only */
/* CPU-only differential test for the 4U MES submit and WB allocator code. */
#include <assert.h>
#include <errno.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include "mes_v12_api_def.h"

typedef uint32_t u32;
typedef uint64_t u64;
typedef int64_t s64;
typedef int spinlock_t;
#ifndef MODEL_SLOTS
#define MODEL_SLOTS 4
#endif
enum { SLOTS = MODEL_SLOTS, WB_BASE = 0x8000, PRIVATE_BASE = 0xc000, RING_FENCE = 0x20000, T = 2100000 };
static const u64 fw_error = UINT64_C(0x8100140700000000);
struct amdgpu_device {
    void *dev;
    struct { struct mes_v12_inv_wb *inv_wb; } mes;
    struct { u64 gpu_addr; u32 *wb; spinlock_t lock;
             unsigned long used[(SLOTS + 63) / 64]; unsigned num_wb; } wb;
};
struct amdgpu_ring { struct { u32 sync_seq, num_fences_mask; u64 gpu_addr; } fence_drv; };
struct mes_v12_inv_wb;
struct amdgpu_mes { struct mes_v12_inv_wb *inv_wb; struct amdgpu_device *adev; struct amdgpu_ring ring[2];
                    spinlock_t ring_lock[2]; };
static struct amdgpu_device dev;
static struct amdgpu_mes mes;
static union { u64 q[(2 * SLOTS + 1) * 4]; u32 d[(2 * SLOTS + 1) * 8]; } memory;
static int last_writer[2 * SLOTS + 1], request, frames, commits, undos;
static struct MES_API_STATUS api[2], query[2];
static u64 now, visible_seq, grace, one_query_delay = 2, one_status_delay = 1, one_status = 1;
static int errors;
static char last_error[256];
static int amdgpu_emu_mode, halt_if_hws_hang;
enum scenario { STALE_SHARED, STALE_DISTINCT, CURRENT_LATE, CURRENT_ERROR, IMMEDIATE_OK };
static enum scenario schedule_id;
static bool ring_full, ring_alloc_error, only_one_request, permanent_zero;
struct event { u64 time, address, value; int writer; bool delivered; };
static struct event events[4];
static size_t event_count;
static void enqueue(u64 time, struct MES_API_STATUS status, u64 value)
{
    assert(event_count < 4 && time >= now);
    events[event_count++] = (struct event){ time, status.api_completion_fence_addr, value, request, false };
}
static unsigned slot_of(u64 address)
{
    if (address >= PRIVATE_BASE) {
        assert((address - PRIVATE_BASE) % 32 == 0);
        unsigned slot = (address - PRIVATE_BASE) / 32;
        assert(slot <= SLOTS);
        return SLOTS + slot;
    }
    assert(address >= WB_BASE && (address - WB_BASE) % 32 == 0);
    unsigned slot = (address - WB_BASE) / 32;
    assert(slot < SLOTS);
    return slot;
}
static void advance(u64 until)
{
    assert(until >= now);
    for (;;) {
        struct event *first = NULL;
        for (size_t i = 0; i < event_count; i++)
            if (!events[i].delivered && events[i].time <= until &&
                (!first || events[i].time < first->time))
                first = &events[i];
        if (!first) break;
        if (first->address == RING_FENCE) {
            assert(first->value > visible_seq);
            visible_seq = first->value;
        } else {
            unsigned slot = slot_of(first->address);
            memory.q[4 * slot] = first->value;
            last_writer[slot] = first->writer;
        }
        first->delivered = true;
    }
    now = until;
}
static void udelay(unsigned us) { if (us == 1) grace++; advance(now + us); }
static unsigned long find_first_zero_bit(const unsigned long *bits, unsigned n)
{
    for (unsigned i = 0; i < n; i++)
        if (!(bits[i / 64] & (1UL << (i % 64)))) return i;
    return n;
}
#define spin_lock_irqsave(lock, flags) do { while (__atomic_exchange_n((lock), 1, __ATOMIC_ACQUIRE)) {} (flags) = 0; } while (0)
#define spin_unlock_irqrestore(lock, flags) do { assert(!(flags)); __atomic_store_n((lock), 0, __ATOMIC_RELEASE); } while (0)
#define __set_bit(n, bits) ((bits)[(n) / 64] |= 1UL << ((n) % 64))
#define __clear_bit(n, bits) ((bits)[(n) / 64] &= ~(1UL << ((n) % 64)))
#define READ_ONCE(value) __atomic_load_n(&(value), __ATOMIC_RELAXED)
#define WRITE_ONCE(loc, value) __atomic_store_n(&(loc), (value), __ATOMIC_RELAXED)
#if HAS_PRIVATE_WB
typedef uint8_t u8;
struct amdgpu_bo { unsigned unused; };
#define BIT_ULL(n) (UINT64_C(1) << (n))
#define AMDGPU_MAX_WB SLOTS
#define DECLARE_BITMAP(name, n) unsigned long name[((n) + 63) / 64]
#define for_each_set_bit(i, bits, n) for ((i) = 0; (i) < (n); (i)++) if ((bits)[(i) / 64] & (1UL << ((i) % 64)))
#define spin_lock_init(lock) (*(lock) = 0)
#define amdgpu_ip_version(adev, type, instance) (fake_gfx)
#define IP_VERSION(a, b, c) (10000 * (a) + 100 * (b) + (c))
#define PAGE_SIZE 4096
#define AMDGPU_GEM_DOMAIN_GTT 2
#define GFP_KERNEL 0
static int fake_gfx, fail_alloc, bo_allocs, bo_frees;
static struct amdgpu_bo fake_bo;
static void *kzalloc(size_t n, int flags) { assert(!flags); return fail_alloc == 1 ? NULL : calloc(1, n); }
#define kfree free
static int amdgpu_bo_create_kernel(struct amdgpu_device *adev, unsigned long n, int align,
                                    u32 domain, struct amdgpu_bo **bo, u64 *addr, void **cpu)
{
    assert(adev == &dev && n == (SLOTS + 1) * 32 && align == PAGE_SIZE && domain == AMDGPU_GEM_DOMAIN_GTT);
    if (fail_alloc == 2) return -ENOMEM;
    ++bo_allocs; *bo = &fake_bo; *addr = PRIVATE_BASE; *cpu = &memory.q[SLOTS * 4];
    return 0;
}
static void amdgpu_bo_free_kernel(struct amdgpu_bo **bo, u64 *addr, void **cpu)
{
    assert(*bo == &fake_bo && *addr == PRIVATE_BASE && *cpu == &memory.q[SLOTS * 4]);
    *bo = NULL; *addr = 0; *cpu = NULL; bo_frees++;
}
#endif
#define cpu_relax() ((void)0)
#define NSEC_PER_USEC 1000
#define min_t(type, a, b) ((type)(a) < (type)(b) ? (type)(a) : (type)(b))
#define lower_32_bits(x) ((u32)(x))
#define upper_32_bits(x) ((u32)((u64)(x) >> 32))
#define amdgpu_sriov_vf(adev) false
static void model_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void model_log(const char *fmt, ...) { (void)fmt; }
static void error_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void error_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt); vsnprintf(last_error, sizeof(last_error), fmt, ap); va_end(ap);
    errors++;
}
#define dev_err(device, ...) error_log(__VA_ARGS__)
#define dev_dbg(device, ...) model_log(__VA_ARGS__)
#define dev_warn(device, ...) model_log(__VA_ARGS__)
#define dev_err_ratelimited(device, ...) error_log(__VA_ARGS__)
static void schedule(void) { abort(); }
static u32 amdgpu_fence_read(struct amdgpu_ring *ring) { (void)ring; return (u32)visible_seq; }
static int amdgpu_ring_alloc(struct amdgpu_ring *ring, unsigned n)
{
    assert(ring == &mes.ring[1] && n == 2 * API_FRAME_SIZE_IN_DWORDS);
    frames = 0;
    return ring_alloc_error ? -ENOMEM : 0;
}
static void amdgpu_ring_undo(struct amdgpu_ring *ring) { (void)ring; assert(ring_full); ++undos; }
static void amdgpu_ring_write_multiple(struct amdgpu_ring *ring, const void *pkt, unsigned n)
{
    assert(ring == &mes.ring[1] && n == API_FRAME_SIZE_IN_DWORDS);
    if (frames++ == 0) {
        const union MESAPI__INV_TLBS *p = pkt;
        assert(p->header.opcode == MES_SCH_API_INV_TLBS);
        api[request] = p->api_status;
        unsigned slot = slot_of(api[request].api_completion_fence_addr);
        if (slot < SLOTS) assert(dev.wb.used[0] & (1UL << slot));
        if (slot != 2 * SLOTS) assert(memory.q[4 * slot] == 0);
        last_writer[slot] = -1; /* The helper just zeroed this allocation. */
    } else {
        const union MESAPI__QUERY_MES_STATUS *p = pkt;
        assert(p->header.opcode == MES_SCH_API_QUERY_SCHEDULER_STATUS);
        query[request] = p->api_status;
        assert(query[request].api_completion_fence_addr == RING_FENCE);
        assert(query[request].api_completion_fence_value == ring->fence_drv.sync_seq);
    }
}
static void amdgpu_ring_commit(struct amdgpu_ring *ring)
{
    (void)ring;
    assert(frames == 2);
    ++commits;
    if (only_one_request) {
        enqueue(now + one_query_delay, query[request], query[request].api_completion_fence_value);
        if (!permanent_zero) enqueue(now + one_status_delay, api[request], one_status);
        return;
    }
    bool stale = schedule_id == STALE_SHARED || schedule_id == STALE_DISTINCT;
    if (!request) {
        assert(now == 0);
        enqueue(T + 1, query[0], query[0].api_completion_fence_value);
        enqueue(stale ? T + 3 : T - 1, api[0], api[0].api_completion_fence_value);
    } else {
        assert(now == T);
        enqueue(T + 2, query[1], query[1].api_completion_fence_value);
        bool success = schedule_id == CURRENT_LATE || schedule_id == IMMEDIATE_OK;
        enqueue(stale ? T + 5 : schedule_id == IMMEDIATE_OK ? T + 1 : T + 3,
                api[1], success ? api[1].api_completion_fence_value : fw_error);
    }
}
static const char *mes_v12_0_get_op_string(union MESAPI__MISC *p)
{ (void)p; return "INVALIDATE_TLBS"; }
static const char *mes_v12_0_get_misc_op_string(union MESAPI__MISC *p)
{ (void)p; return NULL; }

#include "extracted.inc"

typedef int (*submit_fn)(struct amdgpu_mes *, int, void *, int, int);
static int submit_request(submit_fn submit)
{
    union MESAPI__INV_TLBS p = {0};
    p.header.type = MES_API_TYPE_SCHEDULER;
    p.header.opcode = MES_SCH_API_INV_TLBS;
    p.header.dwsize = API_FRAME_SIZE_IN_DWORDS;
    return submit(&mes, 1, &p, sizeof(p), offsetof(union MESAPI__INV_TLBS, api_status));
}
static void test(const char *version, submit_fn submit, enum scenario which, const char *name)
{
    memset(&dev, 0, sizeof(dev)); memset(&mes, 0, sizeof(mes)); memset(&memory, 0, sizeof(memory));
    now = visible_seq = event_count = 0;
    commits = undos = 0;
    dev.wb.gpu_addr = WB_BASE; dev.wb.wb = memory.d;
    dev.wb.used[0] = 1; dev.wb.num_wb = SLOTS; /* Slot 0 belongs to another client. */
    mes.adev = &dev; mes.ring[1].fence_drv.gpu_addr = RING_FENCE;
    mes.ring[1].fence_drv.num_fences_mask = 63;
    schedule_id = which;
#if HAS_PRIVATE_WB
    bool owned = submit == candidate_submit;
    if (owned) {
        fake_gfx = IP_VERSION(12, 0, 1);
        mes_v12_inv_wb_init(&dev);
        assert(dev.mes.inv_wb);
        mes.inv_wb = dev.mes.inv_wb;
    }
#else
    const bool owned = false;
#endif
    request = 0;
    int rc_a = submit_request(submit);
    assert(rc_a == -ETIMEDOUT && now == T && dev.wb.used[0] == 1);
    u32 hold = 0;
    if (which == STALE_DISTINCT && !owned) {
        assert(!amdgpu_wb_get(&dev, &hold));
        assert(WB_BASE + 4 * hold == api[0].api_completion_fence_addr);
    }
    request = 1;
    int rc_b = submit_request(submit);
    unsigned int b_slot = slot_of(api[1].api_completion_fence_addr);
    if (b_slot < SLOTS) assert(!(dev.wb.used[b_slot / 64] & (1UL << (b_slot % 64))));
    int seen_writer = last_writer[slot_of(api[1].api_completion_fence_addr)];
    u64 returned_at = now;
    bool shared = api[0].api_completion_fence_addr == api[1].api_completion_fence_addr;
    bool stale = which == STALE_SHARED || which == STALE_DISTINCT;
    assert(shared == (owned ? !stale : which != STALE_DISTINCT));
    advance(T + 6);
    u64 final = memory.q[4 * slot_of(api[1].api_completion_fence_addr)];
    assert(event_count == 4 && visible_seq == 2);
    for (size_t i = 0; i < event_count; i++) assert(events[i].delivered);
    bool b_failed = which == STALE_SHARED || which == STALE_DISTINCT || which == CURRENT_ERROR;
    assert(final == (b_failed ? fw_error : api[1].api_completion_fence_value));
    if (which == STALE_DISTINCT && !owned) amdgpu_wb_free(&dev, hold);
    assert(dev.wb.used[0] == 1 && commits == 2);
#if HAS_PRIVATE_WB
    if (owned) {
        mes_v12_inv_wb_fini(&dev);
        assert(!dev.mes.inv_wb);
    }
#endif
    printf("{\"version\":\"%s\",\"schedule\":\"%s\",\"A_rc\":%d,\"B_rc\":%d,"
           "\"A_wb_addr\":%" PRIu64 ",\"B_wb_addr\":%" PRIu64 ",\"B_query_seq\":%" PRIu64
           ",\"last_status_writer_when_B_returned\":\"%s\",\"B_return_us\":%" PRIu64
           ",\"B_own_terminal_status\":\"0x%016" PRIx64 "\",\"false_success\":%s}\n",
           version, name, rc_a, rc_b, api[0].api_completion_fence_addr,
           api[1].api_completion_fence_addr, query[1].api_completion_fence_value,
           seen_writer < 0 ? "CPU clear" : seen_writer == 0 ? "A" : "B", returned_at,
           final, rc_b == 0 && b_failed ? "true" : "false");
}
#if HAS_PRIVATE_WB
static void reset_one(void)
{
    memset(&dev, 0, sizeof(dev)); memset(&mes, 0, sizeof(mes)); memset(&memory, 0, sizeof(memory));
    now = visible_seq = grace = event_count = 0;
    commits = undos = errors = 0; last_error[0] = 0;
    request = 0; only_one_request = true;
    ring_full = ring_alloc_error = permanent_zero = false;
    one_status = 1; one_status_delay = 1; one_query_delay = 2;
    dev.wb.gpu_addr = WB_BASE; dev.wb.wb = memory.d;
    dev.wb.used[0] = 1; dev.wb.num_wb = SLOTS;
    mes.adev = &dev; mes.ring[1].fence_drv.gpu_addr = RING_FENCE;
    mes.ring[1].fence_drv.num_fences_mask = 63;
    fake_gfx = IP_VERSION(12, 0, 1); fail_alloc = 0;
    mes_v12_inv_wb_init(&dev);
    assert(dev.mes.inv_wb); mes.inv_wb = dev.mes.inv_wb;
}

static void finish_one(const char *name)
{
    assert(dev.wb.used[0] == 1);
    mes_v12_inv_wb_fini(&dev);
    assert(!dev.mes.inv_wb && bo_allocs == bo_frees);
    printf("{\"check\":\"%s\",\"pass\":true,\"extra_delay_us\":%" PRIu64 ","
           "\"commits\":%d,\"undo\":%d,\"error_logs\":%d}\n",
           name, grace, commits, undos, errors);
}

static void check_control_paths(void)
{
    reset_one();
    assert(!submit_request(candidate_submit) && grace == 0 && commits == 1 && !errors);
    assert(!mes.inv_wb->used[0]); finish_one("immediate_success_without_delay");
    reset_one(); permanent_zero = true;
    assert(submit_request(candidate_submit) == -ETIMEDOUT && grace == 100 && commits == 1);
    assert(errors == 1 && strstr(last_error, "status=0x00000000_00000000") && mes.inv_wb->pending[0] == 1);
    finish_one("never_written_100us_cap_and_log");
    reset_one(); one_query_delay = T - 2; permanent_zero = true;
    assert(submit_request(candidate_submit) == -ETIMEDOUT && grace == 2 && now == T && commits == 1 && errors == 1);
    finish_one("remaining_fence_budget_2us");
    reset_one(); one_query_delay = T + 2;
    assert(submit_request(candidate_submit) == -ETIMEDOUT && !grace && commits == 1 && errors == 1);
    assert(!mes.inv_wb->used[0]);
    finish_one("ring_fence_timeout_with_own_terminal_success");
    reset_one(); ring_alloc_error = true;
    assert(submit_request(candidate_submit) == -ENOMEM && !commits && !undos && !mes.inv_wb->used[0]);
    finish_one("unsubmitted_ring_alloc_failure_releases_slot");
    reset_one(); ring_full = true; mes.ring[1].fence_drv.sync_seq = 63;
    assert(submit_request(candidate_submit) == -ETIMEDOUT && !commits && undos == 1 && !mes.inv_wb->used[0]);
    finish_one("unsubmitted_ring_fence_timeout_releases_slot");
    const u64 malformed[] = {2, UINT64_C(0x8100140700000001), UINT64_C(0x1234567800000001)};
    for (size_t i = 0; i < sizeof(malformed)/sizeof(*malformed); ++i) {
        reset_one(); one_status = malformed[i];
        assert(submit_request(candidate_submit) == -ETIMEDOUT && !grace && errors == 1 && mes.inv_wb->pending[0] == 1);
        finish_one(i == 0 ? "malformed_low" : i == 1 ? "mixed_error_success" : "unexpected_high_bits");
    }
    reset_one(); one_status = fw_error;
    assert(submit_request(candidate_submit) == -ETIMEDOUT && !grace && errors == 1 && !mes.inv_wb->used[0]);
    assert(strstr(last_error, "status=0x81001407_00000000")); finish_one("firmware_error_snapshot");

    reset_one();
    struct mes_v12_inv_wb *wb = mes.inv_wb;
    for (unsigned int i = 0; i < SLOTS; i++) {
        assert(mes_v12_inv_wb_get(wb) == i);
        mes_v12_inv_wb_put(wb, i, true);
    }
    assert(mes_v12_inv_wb_get(wb) == SLOTS);
    *mes_v12_inv_wb_ptr(wb, SLOTS) = 1; /* An unrelated overflow request's write. */
    assert(submit_request(candidate_submit) == -ETIMEDOUT && !grace && commits == 1 && errors == 1);
    assert(strstr(last_error, "slots exhausted") && slot_of(api[0].api_completion_fence_addr) == 2 * SLOTS);
    *mes_v12_inv_wb_ptr(wb, 0) = UINT64_C(0x8100140700000001);
    assert(mes_v12_inv_wb_get(wb) == SLOTS);
    *mes_v12_inv_wb_ptr(wb, 1) = fw_error;
    assert(mes_v12_inv_wb_get(wb) == 1 && !(wb->pending[0] & 2));
    assert(mes_v12_inv_wb_get(wb) == SLOTS); /* Another active CPU still owns slot 1. */
    mes_v12_inv_wb_put(wb, 1, false);
    assert(mes_v12_inv_wb_get(wb) == 1);
    *mes_v12_inv_wb_ptr(wb, 2) = 1;
    assert(mes_v12_inv_wb_get(wb) == 2);
    finish_one("exhaustion_submits_without_accepting_overflow_then_reclaims");
}

/* All old writes were discarded in this injected schedule. Each new request
 * actually completes, and the only terminal writes are to the overflow address.
 */
static void check_lost_completions(void)
{
    reset_one();
    permanent_zero = true;
    for (unsigned int i = 0; i < SLOTS; i++) {
        event_count = 0;
        assert(submit_request(candidate_submit) == -ETIMEDOUT);
        assert(slot_of(api[0].api_completion_fence_addr) == SLOTS + i);
    }
    assert(commits == SLOTS && find_first_zero_bit(mes.inv_wb->pending, SLOTS) == SLOTS);
    permanent_zero = false;
    unsigned false_timeouts = 0, unexpected_errors = 0;
    for (unsigned i = 0; i < 16; i++) {
        event_count = 0;
        int rc = submit_request(candidate_submit);
        assert(slot_of(api[0].api_completion_fence_addr) == 2 * SLOTS);
        assert(last_writer[2 * SLOTS] == request && memory.q[2 * SLOTS * 4] == 1);
        false_timeouts += rc == -ETIMEDOUT;
        unexpected_errors += rc != 0 && rc != -ETIMEDOUT;
    }
    assert(commits == SLOTS + 16);
    /* Positive control. One old terminal write arrives at its own address. */
    *mes_v12_inv_wb_ptr(mes.inv_wb, 0) = 1;
    event_count = 0;
    assert(!submit_request(candidate_submit) && slot_of(api[0].api_completion_fence_addr) == SLOTS);
    assert(commits == SLOTS + 17);
    mes_v12_inv_wb_fini(&dev);
    assert(!dev.mes.inv_wb && bo_allocs == bo_frees);
    printf("{\"check\":\"liveness_after_all_old_completions_lost\","
           "\"initial_submitted_requests_without_terminal_reply\":%d,"
           "\"successful_firmware_operations\":16,\"host_timeouts\":%u,"
           "\"subsequent_success_after_one_old_terminal_write\":true,"
           "\"other_host_errors\":%u,\"mock_pool_slots\":%d,\"production_pool_slots\":1024,\"pass\":%s}\n",
           SLOTS, false_timeouts, unexpected_errors, SLOTS,
           false_timeouts || unexpected_errors ? "false" : "true");
}

static pthread_barrier_t begin_phase, acquired_phase, release_phase;
static unsigned thread_slots[SLOTS];
static void *lease_thread(void *id)
{
    unsigned i = (uintptr_t)id;
    for (unsigned round = 0; round < 128; round++) {
        pthread_barrier_wait(&begin_phase);
        thread_slots[i] = mes_v12_inv_wb_get(mes.inv_wb);
        pthread_barrier_wait(&acquired_phase);
        pthread_barrier_wait(&release_phase);
        mes_v12_inv_wb_put(mes.inv_wb, thread_slots[i], false);
    }
    return NULL;
}

static void check_concurrent_owners(void)
{
    reset_one();
    pthread_t threads[SLOTS];
    assert(!pthread_barrier_init(&begin_phase, NULL, SLOTS + 1));
    assert(!pthread_barrier_init(&acquired_phase, NULL, SLOTS + 1));
    assert(!pthread_barrier_init(&release_phase, NULL, SLOTS + 1));
    for (unsigned i = 0; i < SLOTS; i++)
        assert(!pthread_create(&threads[i], NULL, lease_thread, (void *)(uintptr_t)i));
    for (unsigned round = 0; round < 128; round++) {
        pthread_barrier_wait(&begin_phase); pthread_barrier_wait(&acquired_phase);
        unsigned seen = 0;
        for (unsigned i = 0; i < SLOTS; i++) {
            assert(thread_slots[i] < SLOTS);
            seen |= 1U << thread_slots[i];
        }
        assert(__builtin_popcount(seen) == SLOTS && mes_v12_inv_wb_get(mes.inv_wb) == SLOTS);
        pthread_barrier_wait(&release_phase);
    }
    for (unsigned i = 0; i < SLOTS; i++) assert(!pthread_join(threads[i], NULL));
    assert(!pthread_barrier_destroy(&begin_phase) && !pthread_barrier_destroy(&acquired_phase) &&
           !pthread_barrier_destroy(&release_phase));
    assert(!mes.inv_wb->used[0]); finish_one("four_concurrent_owners_128_rounds");
}

static void check_init_fallbacks(void)
{
    for (int mode = 0; mode <= 2; mode++) {
        fake_gfx = mode ? IP_VERSION(12, 0, 1) : IP_VERSION(12, 0, 0);
        fail_alloc = mode;
        mes_v12_inv_wb_init(&dev);
        assert(!dev.mes.inv_wb);
        mes_v12_inv_wb_fini(&dev);
    }
    assert(bo_allocs == bo_frees);
    puts("{\"check\":\"other_gfx_and_allocation_failures\",\"pass\":true}");
}
#endif

int main(void)
{
#if defined(FULL_CAPACITY_ONLY) && HAS_PRIVATE_WB
    check_lost_completions();
    return 0;
#endif
    const char *names[] = {"A_late_B_error_same_slot", "A_late_B_error_distinct_slot",
                           "B_own_late_success", "B_own_late_error", "B_own_immediate_success"};
    for (unsigned i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
        test("installed_source", stock_submit, i, names[i]);
        test("candidate", candidate_submit, i, names[i]);
    }
#if HAS_PRIVATE_WB
    check_control_paths();
    check_lost_completions();
    check_concurrent_owners();
    check_init_fallbacks();
#endif
    return 0;
}
