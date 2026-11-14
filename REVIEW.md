# TierLab 代码交付与审阅修复报告 (REVIEW.md)

本文件供审阅者逐文件检查代码与回归测试时参考。针对审阅中发现的"原 MockLLM 不读提示词、直接返回预设答案导致接真实模型无法工作及策略对比存在结构偏差"的问题，我们全面升级了测试基础设施并完成了 15 项核心修复，每一项均配备了针对性的回归测试。

---

## 1. 实际运行情况

**已实际运行并全部通过测试。** 本次交付在 Windows 环境（Python 3.11.14）下执行了完整的单元测试、15 项回归测试与真实模拟端到端运行流程：

### 运行的命令与结果
1. `pytest`
   - **结果**：**65 个测试用例全部通过（65 passed, 1 warning, 0 failed）**。耗时 ~15 秒，不需要网络、GPU 或付费 API key。
   - 包含针对 A 组阻塞问题、B 组统计与评测正确性问题的全部回归测试，以及三大任务在 `realistic` 模式下的端到端集成测试。
2. `tierlab run --task math --policy tierlab --backend mock --seed 0`
   - **结果**：顺利完成，产出 JSONL 账本，终态分数 0.8748，云端花费 ¥0.00396，各项字段分列记录。
3. `tierlab run --task code --policy tierlab --backend mock --seed 0`
   - **结果**：顺利完成，参考实现与候选实现分别在独立子进程中执行，产生真实加速比记录。
4. `tierlab run --task paper --policy tierlab --backend mock --seed 0`
   - **结果**：顺利完成，完成全部题目的严格引文校验与事实/综合题分列计分。
5. `tierlab report runs/ --output report.html`
   - **结果**：成功从账本生成 100% 自包含的静态离线 HTML 看板，图表采用 Base64 嵌入，完整绘制质量–云端花费、质量–总成本、质量–墙钟时间三条曲线。

---

## 2. 审阅问题修复与回归测试映射表

针对代码审阅指出的问题，我们新增了“贴近真实”的 `realistic` Mock 模式（返回 `<think>...</think>` 推理内容、文字解释和 Markdown 代码围栏，并严格检查提示词上下文是否包含题意要求，缺项则返回无关内容），并实现了以下 15 项修复：

| 编号 | 修复分类与内容 | 对应回归测试名称 (`tests/test_fixes_regression.py`) | 状态 |
| :--- | :--- | :--- | :---: |
| **A.1** | **提示词内容**：给 `ResearchTask` 增加 `context_for_model()`，本地提示和云端 solve 包含完整当前最优候选、输入输出约定、可用库、超时；论文包含论文全文与全部题目；hint 包含状态卡与候选截断。 | `test_regression_01_prompt_context` | 通过 |
| **A.2** | **输出提取**：各任务实现 `extract_candidate(raw)`，剥离 `<think>...</think>`，提取最后一个代码围栏（python/json），无围栏才用原文，所有输出进验证器前必过。 | `test_regression_02_extract_candidate` | 通过 |
| **A.3** | **格式失败上限**：连续格式失败超过 $N$ 次（默认 2）后按普通停滞升级；运行时崩溃与超时不计入格式失败；采用精确集合匹配。 | `test_regression_03_format_failure_limit_and_signatures` | 通过 |
| **A.4** | **状态卡数据源**：候选被接受时将验证器 `evidence` 和 `score` 写入 `candidate.metadata`，状态卡严格从此处读取。 | `test_regression_04_state_card_reads_from_metadata` | 通过 |
| **A.5** | **初始分数与任意 N 网格**：删除初始候选硬编码分数，一律由 `run_ladder` 算出；数学任务初始网格在任意 $n$ 下保证无重叠无越界（容差 1e-9）；$n \ne 26$ 时不按 2.635 归一化。 | `test_regression_05_initial_score_and_arbitrary_n_grid` | 通过 |
| **A.6** | **配置严格性**：`ControllerConfig` 禁止未知字段；`k_init`、`enable_lessons`、`weak_backend`、`local_patience` 支持 CLI、batch yaml、serve 请求；`default.yaml` 键名对齐，`--config` 真正生效。 | `test_regression_06_config_forbid_unknown_and_fields` | 通过 |
| **B.7** | **统一停止规则**：所有策略统一使用连续 $P$ 步无提升停止（`stagnation_patience`），删除了“分数达到 1.0 才停”的硬编码逻辑。 | `test_regression_07_unified_stopping_rule` | 通过 |
| **B.8** | **论文评分严格化**：分母为全部题目，未答按 0 分；每题必过引文校验；引文要求规范化空白后的精确子串匹配且长度 $\ge 15$；删除词袋模糊匹配；答案比对用精确匹配或 Token F1 并限制长度，禁止无界子串匹配。 | `test_regression_08_paper_qa_strict_scoring` | 通过 |
| **B.9** | **代码任务计时隔离**：参考实现与候选实现分别在独立子进程中运行，每次计时生成新输入；输入规模扩大保证参考耗时 $\ge 0.2$ 秒；加速比不在 6 倍封顶，直接报告真实加速比。 | `test_regression_09_code_optimization_independent_timing` | 通过 |
| **B.10** | **记账与租金分离**：弱模型走云端 API 时记入 cloud 列且 `gpu_s=0`；每行增加 `verify_s`；运行级别记录起止时间（墙钟用终点减起点）；单独报告整段运行 GPU 租金（墙钟 $\times$ 租金），与实际忙碌秒数分开。 | `test_regression_10_accounting_cloud_weak_verify_s_gpu_lease` | 通过 |
| **B.11** | **提示归因 (credited_to)**：hint 之后的本地步骤若产生提升，账本行记录 `credited_to` 指向该次 hint；各档统计和低 ROI 停止规则均按归因后的 $\Delta$ 计算。 | `test_regression_11_hint_attribution` | 通过 |
| **B.12** | **local_patience 决策扫描**：使用 `local_patience`（咨询前允许的本地停滞轮数）替换价格公式里的 $\alpha$ 比较，作为成本–时间前沿的扫描参数；$\alpha$ 仅在报告层折算。 | `test_regression_12_local_patience_decision_rule` | 通过 |
| **B.13** | **BA-AUC 阶梯保持积分**：在固定预算 $B$ 上按阶梯保持（zero-order hold）积分并除以 $B$；云端花费为 0 时同一公式自然得出 `final_score`。 | `test_regression_13_ba_auc_fixed_budget_staircase` | 通过 |
| **B.14** | **沙箱执行结果缓存**：候选在沙箱中的执行结果按代码哈希缓存，避免各级验证器重复起子进程；数学任务执行超时改为可配（默认 30 秒）。 | `test_regression_14_sandbox_cache_and_math_timeout` | 通过 |
| **B.15** | **并发预算预扣**：K 路生成前按最坏情况预扣预算，调用完成后退还；预算超限在发起调用前抛出；并发部分成功时保留有效结果；控制器捕获 `BudgetExceededError` 记为正常停止，不崩溃。 | `test_regression_15_concurrent_budget_reservation` | 通过 |
| **E2E** | **贴近真实数学任务端到端**：Prompt 完整性检验 + `<think>`/代码块提取 + 阶梯验证。 | `test_realistic_mock_e2e_math` | 通过 |
| **E2E** | **贴近真实代码优化端到端**：Prompt 完整性检验 + 独立进程测量 + 加速比检验。 | `test_realistic_mock_e2e_code` | 通过 |
| **E2E** | **贴近真实论文分析端到端**：Prompt 完整性检验 + 全文引文定位 + 事实/综合分项打分。 | `test_realistic_mock_e2e_paper` | 通过 |

---

## 3. 仓库文件树

```
e:\tierlab\
├── pyproject.toml                     # 项目打包与依赖声明，暴露 tierlab CLI 入口
├── README.md                          # 框架介绍、快速上手指南与架构说明
├── REVIEW.md                          # 本审阅修复报告
├── configs/
│   ├── models.yaml                    # 模型名称、API 地址、单价与 GPU 租金配置
│   ├── default.yaml                   # 严格对齐 ControllerConfig 的默认运行配置
│   └── batch_experiment.yaml          # 多任务 x 多策略 x 多种子批量评测配置
├── data/
│   └── papers/
│       ├── sample_paper_1.txt         # 框架验证专用合成占位论文文本
│       └── questions.json             # 明确标注的占位示例题目与标准答案（含题型与引文源）
├── integrations/
│   └── deerflow/
│       ├── tools.py                   # 纯 HTTP 调 tierlab serve 的薄客户端工具
│       └── SKILL.md                   # DeerFlow Agent 调用的技能说明文档
├── src/
│   └── tierlab/
│       ├── types.py                   # 数据模型（ControllerConfig extra="forbid", LedgerEntry credited_to 等）
│       ├── config.py                  # YAML 配置文件读取与配置哈希校验
│       ├── exceptions.py              # 统一异常定义（BudgetExceededError 等）
│       ├── sandbox.py                 # 子进程隔离执行器（含执行哈希缓存机制 #14）
│       ├── state_card.py              # 结构化状态卡纯代码生成与字符上限截断
│       ├── prompts.py                 # 包含 full_context 的提示词构建系统 (#1)
│       ├── lessons.py                 # 教训库（provisional/validated 双状态生命周期管理）
│       ├── ledger.py                  # JSONL 决策账本记录器、归因统计与 BA-AUC 阶梯积分 (#10, #11, #13)
│       ├── gateway.py                 # 统一模型网关（并发预算预扣 #15、Token 统计与硬预算拦截）
│       ├── mock_llm.py                # 支持 realistic 模式（提示词校验 + 思考标签 + 围栏提取）的 Mock 后端
│       ├── p2_interfaces.py           # P2 扩展预留接口
│       ├── controller.py              # 核心控制器主循环（统一停止、格式失败上限、归因追踪、local_patience）
│       ├── report.py                  # 静态离线 HTML 看板生成器（三条曲线 + 租金分列展示）
│       ├── server.py                  # FastAPI 后台服务（支持 k_init, weak_backend, local_patience 等参数）
│       ├── cli.py                     # CLI 命令行入口（run, batch, report, serve）
│       └── tasks/
│           ├── base.py                # ResearchTask 抽象基类（含 context_for_model 与 extract_candidate）
│           ├── math_packing.py        # 数学任务：任意 N 有效网格生成、1e-9 容差、可配超时
│           ├── code_opt.py            # 代码任务：独立进程测试、新数据输入、无上限加速比
│           └── paper_qa.py            # 论文分析任务：全题分母、精确子串匹配、Token F1 答案校验
└── tests/
    ├── test_fixes_regression.py       # 15 项修复针对性回归测试 + 3 项 realistic 模式端到端测试
    ├── test_alpha_conversion.py       # alpha 元/分 到 元/秒 换算的唯一单测 (#9)
    ├── test_gateway.py                # 网关计费与硬预算上限拒绝测试
    ├── test_sandbox.py                # 子进程沙箱超时与安全环境变量测试
    ├── test_verifiers.py              # 三类任务确定性验证器阶梯测试
    ├── test_mock_llm.py               # 经典场景驱动与验证测试
    ├── test_controller.py             # 全策略执行与同 seed 两次运行账本严格一致性测试
    ├── test_lessons.py                # 教训库 provisional/validated 生命周期测试
    ├── test_report.py                 # HTML 离线看板生成测试
    ├── test_server.py                 # FastAPI 异步后台任务测试
    ├── test_cli.py                    # CLI run/batch/report 命令行测试
    └── test_integrations.py           # DeerFlow 客户端工具测试
```

---

## 4. 尚未上机实测的地方（真实硬件与第三方 API）

1. **真实小模型的输出格式稳定性**：
   - *风险*：在真实 4090 上运行量化小模型时，如果未配通 vLLM 的 guided decoding，小模型可能输出非标准 JSON 或未闭合围栏。
   - *已做准备*：代码已实现 `extract_candidate` 正则截取最后一个代码块并剥离 `<think>` 标签，且格式失败已受 `max_format_failures` 保护，超时和崩溃不计入格式失败。
2. **硅基流动（SiliconFlow）API 的实际缓存返回字段**：
   - *状态*：代码遵循标准 OpenAI `usage.prompt_tokens_details.cached_tokens` 解析。需在上机时查看实际返回是否匹配。
3. **真实 GPU 与 API 的网络时延**：
   - *状态*：本地计时和沙箱隔离逻辑已全面通过单元测试与独立进程评测，真实调用只需通过环境变量设置 API key 并在 `configs/models.yaml` 中配置定价与 base_url 即可平滑切换。
