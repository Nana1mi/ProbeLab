# 原生协同界面真实验收

[打开实际对话](http://127.0.0.1:3000/workspace/chats/0b6239c6-2655-4769-86a2-a365ee952135)。研究 ID 为 `run_code_tierlab_173ea03f`，真实模型、同一条自然语言算法优化任务。

小模型 7 次、大模型 2 次（Hint、Solve 各一次），Solve 方案通过验证并被采纳。独立最终加速比 2.0454×，参考 0.3406 秒、候选 0.1665 秒。研究用时 193.34 秒，API 费用 ¥0.110530（含大小模型，不含聊天 Agent）。

- [原生协同卡片](overview.png)、[聊天界面](native-chat.png)、[展开候选代码](solution-expanded.png)。
- [Hint 展示](timeline-hint.png)、[手机界面](timeline-mobile.png)、[深色模式](timeline-dark.png)。
- [完整模型任务、输出、候选和验证事件](trace.ndjson)、[状态与配置](status.json)、[模型调用和费用统计](collaboration.json)。
- [被采纳的代码](accepted_solution.py)、[原始报告](report.html)、[原始决策账本](ledger.json)。

第一次 Agent 等待因上游重复调用限制而结束，研究任务继续完成；随后查询同一运行并展示报告，没有重跑。已将默认查询改为 90 秒有界长等待，最大 120 秒，由回归测试覆盖，未关闭全局循环检测。完整实现与验证说明见 [DeerFlow 协同展示](../../DEERFLOW_COLLABORATION.md)。

这是单次任务的优化和协同链路验收，不是与全大模型、全小模型的同条件对比实验。
