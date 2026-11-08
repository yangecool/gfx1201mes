# 4U MES 驱动安装审查 · 2026-11-08

**4U 继续运行原驱动。V2 和 V3 都被可复跑的测试截住了。** V3 已在 4U 的独立目录内，用主机现有 DKMS 签名证书构建了 9 个模块。生产模块、系统 DKMS 注册和当前启动保持原状。[签名构建回执](results/20261108-install-review/v3-signed-build.json) · [运行模块核验](results/20261108-install-review/production-driver.txt)

| CPU 故障注入 | 原驱动源码 | V2 | V3 |
| --- | --- | --- | --- |
| A 超时，A 的成功回执迟到，B 自己写回错误 | B 返回 `-110` | **B 返回 `0`，误报成功** | B 返回 `-110` |
| B 自己的成功回执晚于调度队列的完成标记 | B 返回 `-110` | B 返回 `0` | B 返回 `0` |
| B 自己写回固件错误 | B 返回 `-110` | B 返回 `-110` | B 返回 `-110` |
| 所有专用地址都等不到原回执，之后 16 个新请求各自成功写回 | 无此专用地址池 | 无此专用地址池 | **16 次都返回 `-110`** |

## 两个失败分别发生在哪里

V2 在调度队列已完成、状态仍为零时，最多再等 100 微秒，接受读到的第一个 `1`。测试执行目标版本的 WB 分配、完整提交、fence 等待、超时释放和重新分配。原请求 A 和新请求 B 都拿到模拟地址 `0x8020`。在 A 返回超时之后，第 2 微秒 B 的队列完成标记可见，第 3 微秒 A 写回 `1`，第 5 微秒 B 写回固件错误 `0x8100140700000000`。V2 在第 3 微秒就报告 B 成功。固定让 B 使用另一个地址后，同样的时序正确返回错误。[V2 每个事件的回执](results/20261108-install-review/v2-ownership.json)

V3 只给 gfx1201 的 `INV_TLBS` 请求分配独立的状态地址。在上面的时序中，A 和 B 实际取得 `0xc000` 和 `0xc020`，A 的迟到写回无法改变 B 的结果。5 个 A/B 场景符合预期。另有 13 个检查覆盖固件错误、日志、提交前失败、立即成功、100 微秒上限、短预算，以及 4 个线程共 128 轮同时取得状态地址。[V3 逐项结果](results/20261108-install-review/v3-ownership.json)

V3 只有 1024 个独立地址。一个已提交的请求始终没有最终回执时，它的地址继续保留。我们用驱动中的 1024 个实际槽位配置执行了 1024 次这样的提交。之后给 16 次新请求都注入自己的成功写回，驱动发出了这 16 个包，却连续返回了 16 次超时。它们写到一个共用的备用地址，驱动无法识别写入者。这个状态能一直持续到旧回执到达或驱动重新初始化。正对照中，给其中一个旧地址补写最终回执，下一次新请求随即能再次正确返回成功。4 个槽位的缩小模型产生同样结果。两组恢复测试都会让进程退出码为 1。[恢复测试和正对照](lifecycle/ownership.c)

板卡、DMA、时间和固件响应由 CPU 模型注入。4U 原版曾真实捕获 4 次队列完成后状态从零变为一，又返回超时；此次没有在 4U 观察到 A/B 误判或 1024 个旧回执丢失，也没有把这些情形归因为 PCI SERR、整机失联或 vLLM/FN 代码。[原驱动实机记录](results/20261107-original-driver-rerun/SUMMARY.json)

## 4U 上实际完成的工作

- 主机内核为 `6.8.0-147-generic`，AMD 驱动为 `7.1.9.31600000`，8 张卡的 MES 与 MES_KIQ 固件均报告 `0x93`。
- 本地用目标内核头构建了九个 DKMS 模块。最终候选在 4U 自身的头文件和签名环境中重新构建。4U 全程只执行了隔离的 `dkms add` 和 `dkms build`。签名者和公钥标识与原模块一致。[构建与源码指纹](results/20261108-install-review/build.json) · [4U 模块清单](results/20261108-install-review/v3-signed-build.json)
- 4U 上隔离构建的 `amdgpu.ko.zst` 的 `srcversion` 为 `2A34331CFB220F1DE31A2C3`，正在运行的是 `8CF47837F896F00244D880B`。当前启动 ID 与构建前相同。重新启动同一内核仍会选择已安装的原驱动。[生产状态](results/20261108-install-review/production-driver.txt)
- 本轮只验证了 CPU 侧控制流与模块签名。V3 的吞吐、延迟、复位后的硬件行为与实际固件回执需要硬件实验。

## 复跑

准备目标版本的已解包 DKMS 源码、内核的 `include/linux/iopoll.h`，以及应用对应补丁后的 `mes_v12_0.c`。在仓库根目录运行下列命令。脚本校验原始源码哈希，用 GCC 13、ASan 和 UBSan 编译提取的完整提交函数及 WB 分配和 fence 等待函数。临时测试文件会自动清理。

```sh
python3 lifecycle/run.py \
  --dkms "$AMD_DKMS_SOURCE" \
  --original "$AMD_DKMS_SOURCE/amd/amdgpu/mes_v12_0.c" \
  --iopoll "$KERNEL_HEADERS/include/linux/iopoll.h" \
  --candidate "$PATCHED_SOURCE/amd/amdgpu/mes_v12_0.c" \
  --output /tmp/mes-ownership.json
```

V2 因将旧回执归给新请求返回 1。V3 因全部旧回执丢失后的恢复检查返回 1。此前仅覆盖 V2 单次返回策略的 15 个用例和 139 项断言仍属于直接测试。[V2 直接测试](results/20261108-review/gate-r2/verification/v2/results.json)

## 下一项需要硬件回答的问题

AMD 的 GFX12 接口头文件描述了成功时回写包内指定的 `api_completion_fence_value`。4U 的在用驱动对 `INV_TLBS` 固定传 `1`，给后续查询包传调度序号。后者能回写序号，尚不足以验证 `0x93` 对失效包也能回写请求编号。一个单独的维护实验可以使用专用地址、空闲且与生产地址空间隔离的 PASID，验证非 `1` 值是否被原样写回，同时保留真正的错误状态。实验结果决定能否依靠请求编号，放回已超时请求的地址。这项实验需要加载测试代码，尚未执行。[上游 GFX12 接口](https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/amd/include/mes_v12_api_def.h)
