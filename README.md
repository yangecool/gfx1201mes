# GFX1201 MES investigation tools and evidence

This repository contains the tools, experimental patches and recorded results from a GFX1201 `INV_TLBS` completion investigation on an eight-GPU Linux server. The source history comes from `mesmock`. The AMD-facing investigation report is maintained separately.

The baseline driver produced four instrumented `MES(1) failed to respond to msg=INVALIDATE_TLBS` errors in the recorded rerun. V2 can accept another request's late success in a CPU fault-injection test. V3 prevents that particular misattribution but fails the recovery test after all private completion slots remain unresolved. A separate diagnostic module confirmed five success-value echoes on one idle GPU. The original driver was restored after that experiment. These results and their source fingerprints are recorded below.

## Contents

| Path | Contents |
| --- | --- |
| `repro/` | Bounded TP8 startup workload, original-module probe, read-only SEL queries and provenance |
| `mock_mes_status.py` | Original 18-case comparison of extracted completion code |
| `review_mes_status.py` | 15 direct return and behavior cases, including 139 assertions for V2 |
| `verify_review_gate.py` | Baseline checks, eight behavioral mutations and one compiler-failure check |
| `lifecycle/` | Complete extracted submission, WB allocation and fence-wait code under scheduled CPU fault injection |
| `prepare_cpu_inputs.py` | Prepare independent, hash-checked patch inputs from the investigated AMD source package |
| `patches/` | V1, V2, V3 and the one-shot protocol diagnostic patch |
| `protocol/` | CPU probe-harness test and the executed maintenance script with its original host guards |
| `build_offline.py` | Isolated build against the investigated kernel headers |
| `capture_host_state.py` | Read-only driver/package fingerprint collection |
| `results/20261107-platform/` | Original SEL records, PCIe paths, firmware data and indexed kernel excerpts |
| `results/20261107-initial-investigation/` | Original source/disassembly analysis, initial traces and HIP controls |
| `results/20261107-original-driver-rerun/` | Four matching kernel errors, branch samples, return values and workload records |
| `results/20261107-build-and-mock/` | V1 build and original mock results |
| `results/20261108-review/` | V2 direct checks, gate verification, source excerpts and CPU microbenchmark |
| `results/20261108-install-review/` | V2/V3 ownership and recovery counterexamples, build and signing receipts |
| `results/20261108-protocol-probe/` | Five hardware echo samples, complete experiment logs and restored-driver fingerprints |
| `results/20261109-publication-checks/` | CPU rerun and file-integrity checks for this publication |

The Chinese review documents in the root are dated investigation snapshots. Their statements about pending work apply to those stages. The protocol result and subsequent restore records are in `results/20261108-protocol-probe/`.

## Target inputs

| Input | Recorded identity |
| --- | --- |
| Kernel | Ubuntu `6.8.0-147-generic` |
| AMD DKMS package | `1:7.1.9.31600000-2407772.24.04` |
| Module version | `7.1.9.31600000` |
| MES and MES_KIQ firmware | `0x93` |
| Original MES source SHA-256 | `c32c4ebcdbb9cca62c3464d4043f15c935d986fcc30e6a4600e9cbbfb119ac7b` |
| Original installed module SHA-256 | `229267a6acce562869e9da3f2c46b593c5a0724f96d368c0313f9be7160c74c4` |

Use the corresponding AMD source package and target kernel headers for the source-pinned tests. The package contains the kernel driver source. MES firmware is a separate binary component. Source inputs can be obtained from the [AMD package archive](https://repo.radeon.com/amdgpu/31.60/ubuntu/pool/main/a/amdgpu-dkms/amdgpu-dkms_7.1.9.31600000-2407772.24.04_all.deb).

## Run the CPU checks

Required tools are Python 3, GCC 13 with ASan/UBSan, and `patch`. Extract the AMD package into a private directory with `dpkg-deb -x`. Set `MES_SOURCE` to its `usr/src/amdgpu-7.1.9-2407772.24.04` directory and `MES_IOPOLL` to the target kernel's `include/linux/iopoll.h`. Choose an output directory that does not exist.

```sh
export MES_INPUTS=/tmp/gfx1201mes-cpu-inputs
python3 prepare_cpu_inputs.py \
  --dkms-source "$MES_SOURCE" --output-dir "$MES_INPUTS"

python3 verify_review_gate.py \
  --v1-source "$MES_INPUTS/v1/amd/amdgpu/mes_v12_0.c" \
  --v2-source "$MES_INPUTS/v2/amd/amdgpu/mes_v12_0.c" \
  --kernel-iopoll "$MES_IOPOLL" \
  --output-dir /tmp/gfx1201mes-direct-gate

python3 lifecycle/run.py \
  --dkms "$MES_SOURCE" \
  --original "$MES_SOURCE/amd/amdgpu/mes_v12_0.c" \
  --iopoll "$MES_IOPOLL" \
  --candidate "$MES_INPUTS/v2/amd/amdgpu/mes_v12_0.c" \
  --output /tmp/gfx1201mes-v2.json

python3 lifecycle/run.py \
  --dkms "$MES_SOURCE" \
  --original "$MES_SOURCE/amd/amdgpu/mes_v12_0.c" \
  --iopoll "$MES_IOPOLL" \
  --candidate "$MES_INPUTS/v3/amd/amdgpu/mes_v12_0.c" \
  --output /tmp/gfx1201mes-v3.json

python3 protocol/probe_mock.py \
  --dkms-source "$MES_SOURCE" \
  --candidate-source "$MES_INPUTS/protocol/amd/amdgpu/mes_v12_0.c"
```

The gate verifier should exit `0` after validating all eleven expected outcomes. The V2 lifecycle test should exit `1` for the stale-success counterexample. The V3 lifecycle test should exit `1` for recovery failures with both 4 and 1024 slots. The probe harness should exit `0`. Its firmware replies are simulated. The five actual hardware replies are stored separately in `results/20261108-protocol-probe/samples.json`.

## Hardware records and scope

`repro/run_remote.py` renders the startup experiment by default. Its explicit `--execute` mode requires the recorded host, image, checkpoint/cache paths and an idle GPU environment. The instruction-offset probe is guarded by both the installed module hash and live build-ID note hash. Different binaries require fresh disassembly and probe placement. Historical paths inside raw logs preserve their original values.

`protocol/maintenance_20261108.py` is the exact executed maintenance script. It temporarily loaded a diagnostic module and restored the stock module through a `finally` block and a systemd cleanup action. It retains the original host, stage-directory and binary guards. The production module and initramfs were verified against their baseline hashes after the experiment.

`MES(1)` identifies a MES pipe. GPU identity comes from the PCI BDF on the same log line. The reproduced completion-status window, historical PCI SERR events and host restarts each require their own causal evidence.

## Licensing

Original test code uses GPL-2.0-only where marked. AMD source excerpts retain their original MIT or dual-license notices. Extracted Linux polling code retains its kernel license notice. The repository includes the GPL v2 license in `LICENSE`.
