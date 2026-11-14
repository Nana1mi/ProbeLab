# 第三轮修复与验证报告

基于 `c364c88`，2026-11-14。第二轮提出的 8 项问题均已修复；本轮没有调用真实模型或付费 API。正式实验前仍需配置真实模型名称、明确填写价格，并验证真实硬件与网络。

## 测试结果

- 修改前：完整原测试 **65 passed**，包含 server、integrations 及实际异步执行，未使用包桩。
- 修改前：第一批新增的 27 个回归探针 **27 failed**，覆盖全部 8 项问题。
- 修改后：完整测试 **98 passed, 1 warning，35.93 秒**。
- 另有 6 个新增检查补充父进程计时、并发缓存、NumPy 执行、CLI、账本及导出样例验证。
- 唯一警告来自环境中的 `anyio.pytest_plugin` 使用旧 pytest 类型别名，不影响测试执行。

实际测试环境为 Windows、Python 3.11.14，真实安装的 FastAPI/Starlette/httpx、pytest/pytest-asyncio。命令：

```bash
python -m pytest -q --tb=short
```

本机使用 `C:/Users/rog/miniconda3/envs/kg_project/python.exe` 执行。

## 修复与回归测试映射

全部新增测试位于 `tests/test_round3_regression.py`；原 65 个测试继续保留并通过。

| 项目 | 最终行为 | 回归测试 |
| --- | --- | --- |
| 1. 论文标准答案泄露 | 题目严格白名单只含 qid/type/question。初始候选不再硬编码正确答案。标准答案、评分来源、段落及 split 仅留在验证器内。使用不在论文里的秘密答案哨兵测试。 | `test_r3_01_paper_context_has_no_grading_fields`、`test_r3_exported_samples_have_state_and_no_grading_fields` |
| 2. 反馈进入模型提示词 | local 始终附状态卡；拒绝的候选也更新 recent_results。hint 用刚完成验证的真实反馈；重复候选记录重复及先前失败原因。all_cloud 及其余基线均携带上一轮反馈和迭代号。 | `test_r3_02_local_keeps_state_with_full_context`、`test_r3_02_failure_reaches_next_request`（六策略）、`test_r3_02_hint_uses_failure_from_current_batch` |
| 3. 真实请求参数 | endpoint extra_body 与调用覆盖值递归合并，与 guided decoding 解耦。弱模型默认 2048、hint 默认 400，均可配。空输出为 model/empty_response；空 hint 不生成教训。价格改成 null 占位，real 在创建客户端前拒绝缺失价格。 | `test_r3_03_local_token_cap_and_empty_response`、`test_r3_03_extra_body_sent_without_guided_decoding`（三目标）、`test_r3_03_real_rejects_unconfigured_pricing`、`test_r3_cli_config_and_prompt_dump` |
| 4. 计时篡改 | runner 在 import 候选前保存 _pc，计时使用此引用；import 后及计算后检查时钟身份。父进程返回独立总耗时，开发与最终评测共用异常检查。默认超过 50 倍加速拒绝为 code/suspicious_timing。 | `test_r3_04_clock_monkeypatch_rejected`（开发/最终）、`test_r3_04_runner_pins_clock_before_import`、`test_r3_04_speedup_limit_applies_to_dev_and_final`、`test_r3_04_parent_clock_disagreement_rejected` |
| 5. 引文与 held-out | 每题设置 gold_paragraph 和显式 split。引文须落在指定段，规范化答案须在引文里。开发仅评分 Q1/Q2，最终评分所有题；Q3 验证结果不回流模型。 | `test_r3_05_irrelevant_quote_and_ungrounded_answer_rejected`、`test_r3_05_explicit_dev_and_held_out_split` |
| 6. 缓存污染 | 初始化及 run 启动新缓存，ContextVar 隔离并发运行。benchmark/hidden 不缓存；键包含代码、入口、runner、超时、参数、extra_files 名称与内容哈希。 | `test_r3_06_cache_key_includes_extra_files`、`test_r3_06_benchmarks_are_never_cached`、`test_r3_06_new_run_drops_previous_execution_cache`、`test_r3_06_concurrent_runs_have_independent_caches` |
| 7. 可用库 | allowed_libraries 可配，默认标准库和 NumPy；提示词按配置写出，实际 NumPy 候选正确性检查通过。 | `test_r3_07_numpy_permission_is_configurable`、`test_r3_07_numpy_candidate_runs_correctly` |
| 8. Mock usage | prompt token 按所有消息字符数估算为 ceil(chars/3)，至少 1；不再伪造固定缓存命中。真实后端仍按 provider usage 计费。 | `test_r3_08_mock_usage_scales_with_prompt` |

另补：

- `--dump-prompts [DIR]` 在实际 dispatch 处导出完整 messages、model、target、token 上限和 extra_body。测试：`test_r3_prompt_dump_is_the_actual_dispatch`、`test_r3_cli_config_and_prompt_dump`。
- JSONL summary 和 HTML 包含 cloud_calls/local_calls/stop_reason。测试：`test_r3_summary_counts_calls_and_stop_reason`。
- 咨询动作同一步发生的弱模型调用也完整计入账本，避免 cloud_small 失败 batch 漏记费用。测试：`test_r3_advisor_action_accounts_for_weak_cloud_batch`。
- CLI 完整保留 controller/budget 配置及 task_options；显式传入默认值也能覆盖文件。token/feedback 参数同时接入 serve；batch 保留完整配置。
- 代码得分是加速比，报告纵轴改成自动适配，避免截掉大于 1 的分数。

## 实际提示词与复现

9 份样例见 [docs/prompt_samples/README.md](docs/prompt_samples/README.md)，包括 math、code、paper 各一份 local/hint/solve。文件取自实际 `--dump-prompts` 调用记录，没有手动拼装或修改。每份均含当前分数、失败签名及开发反馈；paper local/solve 包含全部问题文本，但不含评分字段。

```bash
python docs/prompt_samples/generate.py
```

完整复现配置、样例选择规则及 mock 限制均在上述 README 中。所有请求原始记录及各次 summary.json 写入忽略目录 runs/round3_review/。

## 同种子调用次数

下表是本次实际运行结果，原始数据在 [strategy_comparison.json](docs/prompt_samples/strategy_comparison.json)。所有策略使用种子 0、同一份捕获配置：stagnation_patience=4、local_patience=1、K 固定为 1、最多 15 步。默认配置的自适应 K [1,2,4] 保持可用。

| Task | Policy | Cloud calls | Local calls | Stop reason |
| --- | --- | ---: | ---: | --- |
| math | tierlab | 2 | 11 | consultation_ladder_exhausted |
| math | all_cloud | 5 | 0 | consecutive_stagnation_stopped |
| math | all_local | 0 | 12 | consecutive_stagnation_stopped |
| math | cascade | 1 | 11 | consecutive_stagnation_stopped |
| math | static_split | 1 | 12 | consecutive_stagnation_stopped |
| math | no_advice_retry | 0 | 12 | consecutive_stagnation_stopped |
| code | tierlab | 2 | 3 | consultation_ladder_exhausted |
| code | all_cloud | 5 | 0 | consecutive_stagnation_stopped |
| code | all_local | 0 | 7 | consecutive_stagnation_stopped |
| code | cascade | 1 | 6 | consecutive_stagnation_stopped |
| code | static_split | 1 | 7 | consecutive_stagnation_stopped |
| code | no_advice_retry | 0 | 7 | consecutive_stagnation_stopped |
| paper | tierlab | 2 | 4 | consultation_ladder_exhausted |
| paper | all_cloud | 5 | 0 | consecutive_stagnation_stopped |
| paper | all_local | 0 | 6 | consecutive_stagnation_stopped |
| paper | cascade | 1 | 5 | consecutive_stagnation_stopped |
| paper | static_split | 1 | 6 | consecutive_stagnation_stopped |
| paper | no_advice_retry | 0 | 6 | consecutive_stagnation_stopped |

停止阈值统一不等于终止条件完全一致。TierLab 的咨询阶梯耗尽仍可提前停止，报告现在明确列出该原因。代码任务测量有噪声，调用次数也可能随实际测量的提升判定变化。本表是 mock 行为与计数复核，不支持真实质量或费用结论。

## 配置及边界

- controller.local_max_tokens=2048、hint_max_tokens=400、max_feedback_chars=2400；CLI 也有同名连字符参数。
- 代码任务通过 task_options.code 配置 allowed_libraries、max_speedup（默认 50）、timing_tolerance_s（默认 2）。父进程总耗时包含启动、导入和输入生成；异常规则为内部计时超过父计时，或父计时大于 10 × 内部计时 + 容差，或加速比超过上限。正常超过 50 倍的实现也需显式调整上限后再评估。
- NumPy 配置声明可用依赖，不构成候选代码的安全导入白名单。候选仍运行在普通 Python 子进程；时钟检查和父计时是异常检测，不是针对任意恶意代码的 OS 隔离。
- gold_paragraph 是按空行分割后非空文本块的 0-based 索引，包括开头占位声明块，与原 paragraph_idx 定位语义一致。自定义旧题库仍兼容无 split 元数据的读取；正式实验须像附带数据一样填写 split 与 gold_paragraph。
- models.yaml 云端价格为 null，real 启动必报错，须按所选模型明确填写全部单价。mock 内部使用合成价格；结果中的费用不代表硅基流动当前价格。
- endpoint 支持任意 provider extra_body。本地 vLLM 例子用 chat_template_kwargs.enable_thinking=false；硅基流动例子用顶层 enable_thinking=false，需选择支持该开关的模型。
- 参数依据：[OpenAI Python SDK extra_body](https://github.com/openai/openai-python#undocumented-request-params)、[硅基流动 Chat Completions](https://docs.siliconflow.cn/docs/api/chat-completions-post)。Context7 本轮两次解析均连接失败，因此改查官方来源。
