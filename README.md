# MES 完成状态候选补丁与 mock

针对 4U 当前 AMD 驱动的一处完成状态窗口：scheduler fence 已完成，CPU 第一次检查 API 状态低 32 位为 0，随后同一状态在错误分支采样时已为 1，函数仍返回 `-ETIMEDOUT`。

2026-11-08 的故障注入发现，V2 能把旧请求的成功回执算给失败的新请求。V3 的独立地址修正了这个误判，但全部旧回执丢失后会持续误报超时。4U 已在独立目录构建并签名 V3 的 9 个模块，生产环境继续运行原驱动。[安装审查与复跑方法](INSTALL_REVIEW_20261108.md)

## V1 的历史验证结果

| 验证 | 结果 |
|---|---|
| 原版完整模块构建 | 9 个 `.ko` 编译、链接通过 |
| 打补丁后的模块构建 | 增量编译、链接通过；仅 MES 对象和 amdgpu 模块内容变化 |
| 导出符号 / ABI 清单 | `Module.symvers` 前后一致 |
| 用户态 mock | 18/18；AddressSanitizer、UndefinedBehaviorSanitizer 未报错 |
| 原驱动实机对照 | 2026-11-07 18:45:40–18:45:44（北京时间）捕获 4 条同类 MES 错误 |
| 实机退出后 | BMC SEL 318 → 318；没有重启；NUMA 自动平衡保持 0；专用容器已删除 |
| 环境保护核验 | 已加载版本、启动 ID、DKMS 注册、安装包版本及 14 个源码/驱动文件哈希前后一致 |

首次错误于 18:45:40 捕获，18:45:41 开始停止专用容器，退出期间又捕获 3 条。四条均有真实内核日志、错误分支状态采样和 `-110` 返回值对应。该负载属于非必现复现；有限运行未捕获也不能推断问题消失。

实机这一轮运行的是**原驱动**。mock 的通过不能写成 GPU 实机修复通过，MES 现象也不能替代此前 PCI SERR / 整机失去响应的根因分析。

## 文件

- `patches/0001-mes12-confirm-api-status-after-fence.patch`：单文件候选补丁。
- `build_offline.py`：在 `/tmp/cks4u-mes-build-*` 私有源码、头文件副本中构建，只调用构建目标。
- `mock_mes_status.py`：从原版和补丁版源码提取实际判定、日志及释放分支，生成用户态 C mock。
- `repro/`：此前真正捕获 MES 的 r2 八卡模型启动负载、错误分支 bpftrace 探针及只读 BMC SEL 读取器。
- `capture_host_state.py`：只读采集包版本、加载版本、DKMS 状态、启动 ID、NUMA 和文件哈希。
- `UPSTREAM_ISSUES.md`：相关上游 issue 及与本次取证的异同。
- `results/20261107-build-and-mock/`：构建日志、产物哈希、mock 代码及结果、主机前后快照。
- `results/20261107-original-driver-rerun/`：本次原驱动实机复现证据。

仓库不提交驱动二进制、deb、模型权重、完整驱动源码树或密钥。

## 补丁做什么

正常状态非零时保持原有低 32 位成功判定，不进入额外轮询。仅当 scheduler fence 返回正的剩余等待预算、完整 API 状态仍为零时，使用该**剩余名义微秒预算**进行原子轮询。状态出现任意非零值即停止等待，之后仍按低 32 位判断成功或失败，因此不会把高位固件错误信息当作成功，也不会反复等待已明确的失败状态。

fence 等待本身超时或失败时仍报错，且不会以零超时参数进入无限轮询。错误日志使用一次采样的同一状态快照，包含 fence 等待返回值和高/低 32 位。

`READ_ONCE` 只保证 CPU 按代码重新采样，不保证 DMA 可见性或固件写入顺序已被修复。`read_poll_timeout_atomic` 使用延迟计数，预算不是严格的墙钟截止时间；它不会重置一个新的完整超时，但循环开销和抢占可能延长实际耗时。长期全零状态会比原来的立即判错多等待剩余预算。

原有真实超时后的 writeback 槽释放/晚写风险，以及 GMC/KFD 的错误传播问题均未改变。部署前仍需确认固件协议并评估恢复路径，本候选不宣称解决这些问题。

## 离线构建与 mock

目标固定为：

```text
amdgpu-dkms 1:7.1.9.31600000-2407772.24.04
kernel 6.8.0-147-generic, x86_64
gcc-13 13.3.0-6ubuntu2~24.04.1
```

源码来自 [AMD 官方 deb](https://repo.radeon.com/amdgpu/31.60/ubuntu/pool/main/a/amdgpu-dkms/amdgpu-dkms_7.1.9.31600000-2407772.24.04_all.deb)。构建使用安装包解出的干净源码，不使用或修改宿主已安装的 `/usr/src/amdgpu-*`。

以下命令从仓库根目录执行。`/path/to/amdgpu-dkms.deb` 指上述版本的本地安装包；复制头文件的 SSH 命令仅在目标机读取文件。

```bash
MES_BUILD_ROOT=$(mktemp -d /tmp/cks4u-mes-build-XXXXXXXX)
mkdir -p "$MES_BUILD_ROOT/package" "$MES_BUILD_ROOT/headers"
dpkg-deb -x /path/to/amdgpu-dkms.deb "$MES_BUILD_ROOT/package"
ssh CKS4U 'tar -C /usr/src -czf - linux-headers-6.8.0-147 linux-headers-6.8.0-147-generic' \
  | tar -xz -C "$MES_BUILD_ROOT/headers"

MES_SOURCE="$MES_BUILD_ROOT/package/usr/src/amdgpu-7.1.9-2407772.24.04"
cp "$MES_SOURCE/amd/amdgpu/mes_v12_0.c" "$MES_BUILD_ROOT/mes_v12_0.c.pristine"
python3 build_offline.py --root "$MES_BUILD_ROOT" --phase prepare
python3 build_offline.py --root "$MES_BUILD_ROOT" --phase baseline --jobs 8
patch --batch --fuzz=0 --dry-run -d "$MES_SOURCE" -p1 \
  -i "$PWD/patches/0001-mes12-confirm-api-status-after-fence.patch"
patch --batch --fuzz=0 -d "$MES_SOURCE" -p1 \
  -i "$PWD/patches/0001-mes12-confirm-api-status-after-fence.patch"
python3 build_offline.py --root "$MES_BUILD_ROOT" --phase candidate --jobs 8

python3 mock_mes_status.py \
  --baseline-source "$MES_BUILD_ROOT/mes_v12_0.c.pristine" \
  --candidate-source "$MES_SOURCE/amd/amdgpu/mes_v12_0.c" \
  --kernel-iopoll "$MES_BUILD_ROOT/headers/linux-headers-6.8.0-147/include/linux/iopoll.h" \
  --output-dir "$MES_BUILD_ROOT/mock" --sanitize
```

`prepare` 只执行一次：AMD 的原始预处理脚本会改写源码，重复执行不安全。构建工具仅在私有副本中调整头文件路径和 Ubuntu 24.04 宏；这些构建隔离调整与功能补丁分开保存。此次原版完整构建后，在同一树中应用补丁并增量构建。编译日志中有 GCC 命令名不同的提示，但两边版本字符串完全相同；没有提供私有 `vmlinux`，因此跳过 BTF 生成。九个模块均正常链接，未签名、未安装。

mock 使用目标内核 `iopoll.h` 中的实际 `read_poll_timeout_atomic` 宏。原版普通状态读取仅替换成受控读取 accessor；两版的判定控制流、错误日志和 WB 释放分支直接来自输入源码，没有另写一套“修复算法”。覆盖成功、0→1、延迟成功、高位错误、错误晚到、调试位兼容、永久全零、零/负预算、fence 超时及三种日志路径。mock 不模拟 GPU、PCIe、DMA 缓存一致性、实际锁/调度或 TLB 正确性，也不提供性能结论。

已有生成的 C mock 可单独在用户态重跑：

```bash
gcc-13 -std=gnu11 -O2 -Wall -Wextra -Werror \
  -fsanitize=address,undefined -fno-omit-frame-pointer \
  results/20261107-build-and-mock/mock/mes_status_mock.c \
  -o /tmp/mes_status_mock
ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1 /tmp/mes_status_mock
```

## 原驱动实机复现

默认只生成远端程序并做语法检查。加 `--execute` 才会通过 SSH 启动真实八卡 GPU 负载和临时 bpftrace 探针。

```bash
python3 repro/run_remote.py --output-dir /tmp/mesmock-render-only
python3 repro/run_remote.py --host CKS4U --output-dir /tmp/mesmock-original-rerun --execute
```

该入口限定原来的内核、模块版本、磁盘模块 SHA-256 和已加载模块 build-ID note。指令偏移 `+0x3f0`、寄存器和栈偏移只适用于已反汇编核验的原始模块；**不能用于本候选编译出的模块**。变更驱动后必须重新定位探针，不能绕过校验直接沿用偏移。

启动要求 NUMA 自动平衡为 0、无运行容器、无 `/dev/kfd` 使用者，已有指定镜像和模型，以及空闲的服务端口。保持原 TP8、CPU/内存绑定与通信设置，不拉取镜像、不覆盖源码、不设置 sysctl。等待上限 480 秒，出现 MES、内核健康错误、BMC SEL 变化或 NUMA 变化时停止，仅清理本轮创建的容器及探针。

`fence_completed=1` 由已核验错误分支控制流与寄存器推断，不是独立的第二次硬件测量。探针会改变运行时序，因此记录不能当作自然发生率或性能数据。

## 来源与许可

驱动片段来自 AMD 安装包，保留原文件 MIT 许可声明；轮询宏来自目标 Linux 内核头文件，保留 GPL-2.0-only 声明及 Linux Foundation 版权信息。生成的 C mock 同时保留这些声明。新增工具脚本按 GPL-2.0-only 提供，见 `LICENSE`。原始实机工具及历史两次捕获的来源哈希见 `repro/provenance.json`。
