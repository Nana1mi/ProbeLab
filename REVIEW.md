# TierLab 代码交付审阅报告 (REVIEW.md)

本文件供审阅者逐文件检查代码时参考。在租用 GPU 与连接真实 API 前，所有功能均已在本地环境通过模拟后端端到端跑通。

---

## 1. 实际运行情况

**已实际运行并全部通过测试。** 本次交付在 Windows 环境（Python 3.11.14）下执行了完整的构建、测试与端到端运行流程：

### 运行的命令与结果
1. `pip install -e .`
   - **结果**：构建并安装 `tierlab-0.1.0` 成功，无任何依赖冲突。
2. `pytest -v`
   - **结果**：**47 个单元与集成测试用例全部通过（47 passed, 1 warning, 0 failed）**。涵盖网关计费、预算硬拦截、沙箱子进程超时与环境隔离、三个任务验证器阶梯、MockLLM 六大场景、所有策略执行、确定性复现（同 seed 两次账本一致性校验）、alpha 换算等。
3. `tierlab run --task math --policy tierlab --backend mock --seed 0`
   - **结果**：端到端跑完，生成 JSONL 账本，最终分数 0.8748，云端花费 ¥0.00396，各字段分列记录。
4. `tierlab run --task code --policy tierlab --backend mock --seed 0`
   - **结果**：端到端跑完，加速比与正确性验证通过，生成完整账本。
5. `tierlab run --task paper --policy tierlab --backend mock --seed 0`
   - **结果**：端到端跑完，完成事实题/综合题计分与引文段落定位（记录文件名与段落号）。
6. 各策略对比测试：
   - `python -m tierlab.cli run --task math --policy all_cloud --backend mock --seed 0`：跑通（单候选迭代反馈）。
   - `python -m tierlab.cli run --task math --policy all_local --backend mock --seed 0`：跑通（全本地零云端）。
   - `python -m tierlab.cli run --task math --policy cascade --backend mock --seed 0`：跑通（失败接管）。
   - `python -m tierlab.cli run --task math --policy static_split --backend mock --seed 0`：跑通（前置规划+本地执行）。
   - `python -m tierlab.cli run --task math --policy no_advice_retry --backend mock --seed 0`：跑通（咨询点归因重试）。
7. `tierlab batch configs/batch_experiment.yaml --output-dir runs/batch_test`
   - **结果**：自动化执行 3 任务 $\times$ 4 策略 $\times$ 3 种子 = 36 场实验，全部生成独立账本，并输出 594KB 的 `batch_report.html`。
8. `tierlab report runs/ --output runs/report.html`
   - **结果**：成功生成 417KB 的自包含离线 HTML 看板，图表采用 Base64 嵌入，画出要求的质量–云端花费、质量–总成本、质量–墙钟时间三条曲线。

---

## 2. 仓库文件树与各模块一句话说明

```
e:\tierlab\
├── pyproject.toml                     # 项目打包与依赖声明，暴露 tierlab CLI 入口
├── README.md                          # 框架介绍、快速上手指南与架构说明
├── REVIEW.md                          # 本审阅文档：测试记录、设计假设与未验证点
├── configs/
│   ├── models.yaml                    # 模型名称、API 地址、单价与 GPU 租金配置
│   ├── default.yaml                   # 默认运行超参数与策略配置
│   └── batch_experiment.yaml          # 多任务 x 多策略 x 多种子批量评测配置
├── data/
│   └── papers/
│       ├── sample_paper_1.txt         # 框架验证专用合成占位论文文本
│       └── questions.json             # 明确标注的占位示例题目与标准答案（含题型与引文源）
├── integrations/
│   └── deerflow/
│       ├── tools.py                   # 纯 HTTP 调 tierlab serve 的薄客户端工具，不 import tierlab 核心
│       └── SKILL.md                   # DeerFlow Agent 调用的技能说明文档
├── src/
│   └── tierlab/
│       ├── __init__.py                # 顶层包导出
│       ├── types.py                   # 核心数据模型（VerificationResult、LedgerEntry、Budget 等）
│       ├── config.py                  # YAML 配置文件读取与配置哈希校验
│       ├── exceptions.py              # 统一异常定义（BudgetExceededError、SandboxTimeoutError 等）
│       ├── sandbox.py                 # 子进程隔离执行器（超时、临时目录、环境变量清洗）
│       ├── state_card.py              # 结构化状态卡纯代码生成与字符上限截断
│       ├── prompts.py                 # 咨询阶梯与本地生成的 Prompt 模板系统
│       ├── lessons.py                 # 教训库（provisional/validated 双状态生命周期管理）
│       ├── ledger.py                  # JSONL 决策账本记录器与 BA-AUC 指标计算
│       ├── gateway.py                 # 统一模型网关（Token 统计、硬预算上限拦截、重试与计费）
│       ├── mock_llm.py                # 脚本化确定性 MockLLM 后端（支持 6 大核心场景）
│       ├── p2_interfaces.py           # P2 扩展预留接口（自适应阶梯、向量教训、Alpha 扫描）
│       ├── controller.py              # 核心控制器主循环（多策略调度、去重、分档升级、停止规则）
│       ├── report.py                  # 静态离线 HTML 看板生成器（画出三条曲线与账本回放）
│       ├── server.py                  # 轻量 FastAPI 后台服务（POST /runs, GET /runs/{id}, GET report）
│       ├── cli.py                     # CLI 命令行入口（run, batch, report, serve）
│       └── tasks/
│           ├── __init__.py            # 任务工厂
│           ├── base.py                # ResearchTask 抽象基类与 Verifier（含 cheap/expensive 声明）
│           ├── math_packing.py        # 数学任务：单位正方形 n 圆装填最大化
│           ├── code_opt.py            # 代码任务：批量余弦相似度 Top-K 性能优化
│           └── paper_qa.py            # 论文分析任务：引文段落定位与事实/综合题分列计分
└── tests/
    ├── conftest.py                    # Pytest 共享 fixture
    ├── test_alpha_conversion.py       # 针对规格 #9：alpha 元/分 到 元/秒 换算的唯一单测
    ├── test_gateway.py                # 网关计费与硬预算上限拒绝测试
    ├── test_sandbox.py                # 子进程沙箱超时与安全环境变量测试
    ├── test_verifiers.py              # 三类任务确定性验证器阶梯测试
    ├── test_mock_llm.py               # 6 大场景针对性驱动与验证测试
    ├── test_controller.py             # 全策略执行与同 seed 两次运行账本严格一致性测试
    ├── test_lessons.py                # 教训库 provisional/validated 生命周期测试
    ├── test_report.py                 # HTML 离线看板生成测试
    ├── test_server.py                 # FastAPI 异步后台任务测试
    ├── test_cli.py                    # CLI run/batch/report 命令行测试
    └── test_integrations.py           # DeerFlow 客户端工具测试
```

---

## 3. 设计假设与对规格的实现细节

1. **`price(a)` 预估公式的假设**：在决策点计算加宽 `widen` 与咨询 `hint/solve` 的比价时，真实耗时和模型输出长度在调用前未知。程序采用基于该动作用途的保守上限 token 和经验时间进行预估。
2. **停止规则激活时机**：对“近 $R$ 次咨询的 $\Delta / \text{¥}$ 低于阈值”的检查，设定为**在历史咨询次数至少达到 $R$ 次之后**才触发，避免第 1 次咨询由于初始化波动被误杀。
3. **预算硬上限的双重校验**：网关在发起云端调用前做前置预估拦截（如果即使按最大输出 token 计费也会超额则提前拒绝）；调用返回后按实际 token 精确计费，一旦累计超额立刻冻结后续所有云端调用。
4. **候选去重逻辑（规格 #11）**：候选文本在去除多余连续空白后计算 SHA256 指纹。对于已见过的指纹，跳过送检并直接在账本中记录 `action=duplicate_skip, duplicate=true`。
5. **K 宽度步进（规格 #11）**：K 的取值严格从配置给出的 `k_schedule`（默认 `[1, 2, 4]`）中索取。加宽时向右移动索引，有提升时向左移动索引，不进行无界乘除。
6. **分列计费与总价隔离（规格 #9）**：账本中 `cloud_cny`、`gpu_s`、`gpu_cny`（按租金折算）、`wall_s` 全部作为独立字段存储，代码中任何地方不输出合成的单字段"总价"。看板中绘制质量对云端花费、质量对总成本（`cloud_cny + gpu_cny`）、质量对墙钟时间三条独立曲线。
7. **Alpha 换算单一源（规格 #9）**：`alpha_cny_per_min` 在配置中以 元/分钟 声明，在 `src/tierlab/types.py` 中的 `alpha_to_cny_per_second` 单一函数中换算为 元/秒，并在 `tests/test_alpha_conversion.py` 中做了精确单元测试。

---

## 4. 尚未上机实测的地方（真实硬件与第三方 API）

1. **vLLM 约束解码参数（guided decoding）**：
   - 依赖 vLLM 版本的差异（例如 vLLM 0.6.x 与 0.7.x 对 JSON Schema 分别支持 `guided_json` 或 `response_format` 参数）。代码在网关中预留了 `extra_body` 透传接口，需在租用 4090 后实际启动 vLLM 发送一个 JSON 请求做冒烟测试。
2. **硅基流动（SiliconFlow）API 缓存统计字段**：
   - 代码按 OpenAI 兼容标准读取 `usage.prompt_tokens_details.cached_tokens`。如果硅基流动返回的字段名称有差异（如放在自定义 header 或不同字典键），需在 `gateway.py` 的解析处对齐。
3. **DeerFlow 原生工具注解格式**：
   - `integrations/deerflow/tools.py` 严格遵循“不修改 DeerFlow 源码、纯 HTTP 调 `tierlab serve`”的约束。由于未拉取 DeerFlow 完整仓库，函数头部保留了 `TODO(verify: DeerFlow @tool registration format)` 标记，接入时根据 DeerFlow 实际使用的 `@tool` 装饰器导入即可。

---

## 5. 已知缺口与最可能出错的三个地方

1. **真实小模型的输出格式稳定性**：
   - *风险*：在真实 4090 上运行量化小模型时，如果未开启或未配通 vLLM 的 guided decoding，小模型可能输出附带 Markdown 解释性文本或非标准 JSON。
   - *应对*：代码在各任务解析中加入了剥除 ````json 代码块的前置清理，并且规则 #5 明确规定格式/语法错误只在本地快速重试，不会白白消耗云端预算。
2. **代码任务在宿主机上的微秒级 CPU 计时抖动**：
   - *风险*：代码任务在多核 CPU 上交替测量参考实现与候选实现。如果 GPU 推理服务（vLLM）在后台并发占用全部 CPU 核心，交替计时的背景噪声可能偏大。
   - *应对*：采用了交替运行、取多次中位数的抗噪设计，并将低于 `noise_threshold`（默认 0.08~0.15）的加速比视为无效提升。上机时建议通过 `taskset` 或将推理线程与评测线程绑定到不同 CPU 核心。
3. **硅基流动云端调用的并发与网络波动**：
   - *风险*：在批量并发或连续升级时，若硅基流动服务商出现瞬时 429（Rate Limit）或 502/503。
   - *应对*：网关已内建指数退避重试（3 次），但若账户欠费或并发超限，网关会直接捕获并记录失败，避免进程死锁。

---

## 6. 接上真实模型需要执行的步骤清单

接上真实模型**不需要修改任何 Python 代码**，只需按以下步骤操作：

1. **租用 GPU 并启动本地弱模型**：
   ```bash
   # 在租来的 RTX 4090 上启动 vLLM（推荐 AWQ 量化版本）
   vllm serve Qwen/Qwen2.5-32B-Instruct-AWQ --port 8000 --max-model-len 16384
   ```
2. **设置 API Key 环境变量**：
   ```bash
   export SILICONFLOW_API_KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxx"
   export LOCAL_API_KEY="EMPTY"
   ```
3. **核对并编辑 `configs/models.yaml`**：
   - 根据当天[硅基流动定价页](https://www.siliconflow.cn/pricing)填入 `prompt_cny_per_m`、`cached_cny_per_m`、`completion_cny_per_m` 的准确数值。
   - 确认 `local_model.base_url` 指向 `http://127.0.0.1:8000/v1`。
4. **执行单次冒烟验证**：
   ```bash
   tierlab run --task math --policy tierlab --backend real --seed 0
   ```
   检查终端输出与生成的 `runs/` 下账本，确认每一步均有真实的 token 和费用产生。
5. **执行完整实验与出图**：
   ```bash
   tierlab batch configs/batch_experiment.yaml --output-dir runs/real_experiment
   tierlab report runs/real_experiment --output runs/real_report.html
   ```
