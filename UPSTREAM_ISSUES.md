# 相关公开 issue 核对

核对日期：2026-11-07。以下是相关报告，不是本候选补丁已有上游认可或修复有效的证明。

最接近的是 [ROCm/TheRock #9279](https://github.com/ROCm/TheRock/issues/9279)，页面仍为 Open / triage。旧链接 [ROCm/legacy-rocm-build #6630](https://github.com/ROCm/legacy-rocm-build/issues/6630) 已重定向到同一个 issue，不是两份独立报告。

该讨论 10 月 28 日的补充记录了四张 gfx1201、使用内核自带 amdgpu、vLLM 启动和跨卡显存映射附近出现 `INVALIDATE_TLBS` MES 错误，与本次负载和日志接近。页面还讨论其他挂起模式，不能全部视为一种原因。

没有在该页面找到我们这次直接捕获的“scheduler fence 等待成功、第一次 low 32 为 0、错误分支再读完整状态为 1”的对应证据，也未找到明确针对这一窗口的已确认修复。因此判断为**相关性较高，同一根因尚未证实**。

[ROCm/TheRock #9299](https://github.com/ROCm/TheRock/issues/9299) 报告 gfx1201 负载下整机冻结、SMU 不响应和 MES 不可恢复。它与此前主机冻结这一症状相关，但不能据此把我们的 PCI SERR、MES 完成状态窗口和该案例合并为一个 bug。

查询也覆盖了 drm/amd issue 和 amd-gfx 补丁邮件关键词。drm/amd 的部分页面被 Anubis 拒绝访问，因此“未找到直接匹配项”不等于保证所有公开或内部 issue 中都没有。

本轮仅检索，没有向上游创建 issue、评论或提交补丁。
