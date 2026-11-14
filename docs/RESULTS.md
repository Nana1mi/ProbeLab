# 已保存结果与成品入口

这里区分原始付费 API 结果、成本假设、真实 Agent 演示和 mock。仓库中的结果是保存的某次运行，不承诺 API 重新抽样能得到相同文本或性能。

| 材料 | 执行方式 | 已支持的结论 | 入口 |
| --- | --- | --- | --- |
| 代码三策略对照 | Qwen、DeepSeek 的真实 API 搜索；冻结候选后重复计时 | 在本案例与“小模型成本为零”假设下，协同比全大模型强模型费用低 62.2%，产出程序耗时低 13.1% | [案例报告](COLLABORATION_CASE.md)、[原始结果](case_studies/code_seed0/) |
| 自然语言任务首次复跑 | 真实模型、50× 防异常上限 | 触发 Hint/Solve，但没有采纳优化；最终保留初始代码，约 0.93× | [完整失败复盘](case_studies/agent-code-natural-20261114/README.md) |
| DeerFlow 原生协同验收 | 真实模型、50× 防异常上限 | 7 次小模型、2 次强模型；Solve 方案被采纳；独立最终 2.0454× | [界面与结果](case_studies/native-collaboration-20261114/README.md) |
| 数学部分探索 | 真实 API；未完成三策略对照 | 仅作取消/未完成记录，不支持效果比较 | [部分记录](case_studies/math_seed0_partial/) |
| 聊天 Agent 数学流程演示 | 真实聊天模型＋mock 研究输出 | 工具链与 Artifacts 可用，不支持模型质量比较 | [演示元数据](deerflow-agent-demo.json) |

原生验收的费用按角色精确拆分：小模型 ¥0.101506，强模型 ¥0.009024，合计 ¥0.110530；研究耗时 193.34 秒。聊天 Agent 费用另计。独立最终参考 0.3406 秒、候选 0.1665 秒，完整源码和计时证据均保存。未对这一次原生演示另跑两条单模型基线。

三策略对照采用 1000× 异常上限，原生演示采用 50×，运行环境也不同；不可把百倍与两倍的结果合并为同一次实验的进展。双 API 计费口径下，三策略对照的协同费用为全大模型的约 2.16 倍；成本优势只在指定的小模型成本情景下成立。

历史对话中的统计误报、首次验收的重复查询中断及没有成功的运行均保留。代码变更后重新运行模型会增加费用；仅查看报告、事件和截图不调用模型。重新计时冻结代码会产生新的计时记录，应另存目录，避免覆盖这里的原始证据。

成品代码入口：

- [研究控制器](../src/tierlab/controller.py)、[模型网关](../src/tierlab/gateway.py)、[逐次追踪](../src/tierlab/trace.py)。
- [研究服务与 API](../src/tierlab/server.py)、[工作台](../src/tierlab/web/)。
- [DeerFlow 原生协同组件及补丁](../integrations/deerflow/frontend/)、[安装器](../scripts/install_deerflow_collaboration.py)。
- [Docker 构建准备](../scripts/prepare_deerflow_demo.py)、[配置模板](../integrations/deerflow/config.demo.yaml)、[启动器](../scripts/run_deerflow_agent_demo.py)。
- [四分钟六页 PPT 规划与讲稿](presentation/VELA_4MIN_PPT_PLAN.md)。

本机聊天 URL 仅在运行演示服务的电脑可用。GitHub 阅读者可直接查看仓库中的 PNG、Markdown、JSON、代码及下载后离线打开 HTML 报告。

原始证据的字节校验清单见 [RESULTS_SHA256.json](RESULTS_SHA256.json)，可运行
`python scripts/verify_saved_results.py` 检查；不会调用模型或执行候选代码。交付范围与检查结果见 [交付快照](DELIVERY.md)。
