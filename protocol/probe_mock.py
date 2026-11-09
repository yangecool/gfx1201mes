#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile the exact opt-in probe and its resource lifetime against a fake ring."""
from pathlib import Path
import argparse
import tempfile
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--candidate-source', type=Path, required=True)
parser.add_argument('--dkms-source', type=Path, required=True)
args = parser.parse_args()
src = args.candidate_source.read_text()
pre=src[src.index('struct mes_tag_sample {'):src.index('static bool mes_tag_probe;')]
parts=src[src.index('static DEFINE_MUTEX(mes_tag_probe_mutex);'):src.index('static int mes_v12_0_sw_fini(struct amdgpu_ip_block *ip_block)')]
header = args.dkms_source / 'amd/include'
with tempfile.TemporaryDirectory() as temp:
    d=Path(temp)
    (d/'test.c').write_text(r"""
#include <stdint.h>
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "mes_v12_api_def.h"
typedef uint8_t u8; typedef uint32_t u32; typedef unsigned long long u64;
struct amdgpu_bo {unsigned id;};
struct amdgpu_mes { struct {struct {bool ready;} sched;} ring[2]; u32 kiq_version;};
struct amdgpu_device {void *dev; char *pdev; bool enable_uni_mes; struct amdgpu_mes mes;};
struct amdgpu_ip_block {struct amdgpu_device *adev;};
static struct amdgpu_device dev; static struct amdgpu_ip_block ip={&dev};
static bool mes_tag_probe;
#define IP_VERSION(x,y,z) ((x)*10000+(y)*100+(z))
#define amdgpu_ip_version(adev,type,idx) (gfx)
#define pci_name(x) (x)
#define AMDGPU_MES_SCHED_PIPE 0
#define AMDGPU_MES_KIQ_PIPE 1
#define AMDGPU_MES_VERSION_MASK 0xfff
#define ARRAY_SIZE(a) (sizeof(a)/sizeof(*a))
#define DEFINE_MUTEX(name) int name
#define mutex_lock(p) do {assert(!*(p));*(p)=1;} while(0)
#define mutex_unlock(p) do {assert(*(p));*(p)=0;} while(0)
#define PAGE_SIZE 4096
#define AMDGPU_GEM_DOMAIN_GTT 2
static int gfx, mode, writes, pasids, pages;
static u64 actual[5]; static struct amdgpu_bo obj; static u64 memory[512];
static void logfmt(const char *fmt, ...) __attribute__((format(printf,1,2)));
static void logfmt(const char *fmt, ...) {(void)fmt;}
#define dev_warn(adev, ...) logfmt(__VA_ARGS__)
#define dev_info(adev, ...) logfmt(__VA_ARGS__)
static int amdgpu_pasid_alloc(int bits, void *owner) {
    assert(bits==16 && !owner && !pasids); if(mode==0) return -12; ++pasids;return 444;
}
static void amdgpu_pasid_free(int p) {assert(p==444 && pasids==1); --pasids;}
static int amdgpu_bo_create_kernel(struct amdgpu_device *d,int size,int align,int domain,
                                   struct amdgpu_bo **b,u64 *addr,void **cpu) {
    assert(d==&dev && size==PAGE_SIZE && align==PAGE_SIZE && domain==2 && !pages);
    if(mode==1) return -12;
    ++pages;*b=&obj;*addr=0x9000;*cpu=memory;return 0;
}
static void amdgpu_bo_free_kernel(struct amdgpu_bo **b,u64 *a,void **c) {
    assert(*b==&obj && *a==0x9000 && *c==memory && pages==1);--pages;*b=NULL;*a=0;*c=NULL;
}
"""+pre+r"""
static int mes_v12_0_submit_with_probe(struct amdgpu_mes *m,int pipe,void *pkt,int size,int off,struct mes_tag_sample *s) {
    union MESAPI__INV_TLBS *p=pkt;
    assert(m==&dev.mes && pipe==1 && pasids==1 && pages==1 && size==sizeof(*p) &&
           off==offsetof(union MESAPI__INV_TLBS,api_status) && p->header.type==MES_API_TYPE_SCHEDULER &&
           p->header.opcode==MES_SCH_API_INV_TLBS && p->header.dwsize==API_FRAME_SIZE_IN_DWORDS &&
           p->invalidate_tlbs.inv_sel==0 && p->invalidate_tlbs.flush_type==2 &&
           p->invalidate_tlbs.inv_sel_id==444 && p->invalidate_tlbs.hub_id==0);
    assert(s->cpu==memory+4*writes && s->gpu==0x9000U+32U*writes && writes<5);
    if(mode==5) return -12;
    s->submitted=true;s->fence_left=100;
    s->last=mode==3 ? 1 : mode==4 && writes==1 ? UINT64_C(0x8100140700000000) : s->sent;
    actual[writes++]=s->last;
    return 0;
}
"""+parts+r"""
int main(void) {
    gfx=120001; dev.pdev="0000:03:00.0"; dev.enable_uni_mes=true;
    dev.mes.ring[0].sched.ready=true;dev.mes.kiq_version=0x93;
    mes_v12_0_probe_late_init(&ip); assert(!mes_tag_run.attempted);
    mes_tag_probe=true; dev.pdev="0000:13:00.0"; mes_v12_0_probe_late_init(&ip); assert(!mes_tag_run.attempted);
    dev.pdev="0000:03:00.0";
    static const u64 expected[]={1,2,0x005a39c7,0x6a95310d,1};
    for(mode=0;mode<6;mode++) {
        memset(&mes_tag_run,0,sizeof(mes_tag_run)); writes=0;
        assert(!mes_v12_0_probe_late_init(&ip));
        assert(writes==(mode<2||mode==5 ? 0 : mode==4 ? 2 : 5));
        if(mode>=2) assert(pasids==1 && pages==1);
        if(mode==2) assert(!memcmp(actual,expected,sizeof(expected)));
        if(mode==3) for(int i=0;i<5;i++) assert(actual[i]==1);
        int saved=writes; mes_v12_0_probe_late_init(&ip);assert(writes==saved);
        mes_v12_0_probe_fini(&dev); assert(!pages && !pasids);
    }
    puts("opt-in/BDF guards, allocation errors, independent addresses, echo, constant 1, explicit error, pre-submit failure, one-shot and teardown passed");
}
""")
    bin=d/'mock';subprocess.run(['gcc-13','-fsanitize=address,undefined','-fno-sanitize-recover=all','-O2','-no-pie','-Wall','-Wextra','-Werror','-I',str(header),str(d/'test.c'),'-o',str(bin)],check=True)
    subprocess.run([str(bin)],check=True)
