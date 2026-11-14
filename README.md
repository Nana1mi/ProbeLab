# TierLab: Universal Research Agent with Local GPU & Cloud Escalation

TierLab 是一个通用的自主研究 Agent 协作框架。核心经济学设定：**本地 GPU 算力是固定成本，云端 token 是边际成本**。框架通过纯代码构筑的确定性验证器阶梯驱动，在本地用高并发（K-way 投机并行）把固定算力跑满；仅在进展停滞时按最小粒度向云端购买建议，并将经验证有效的建议固化为结构化教训卡，避免同类问题二次消费。

---

## 核心特性

- **模型网关与预算硬上限**：所有模型调用统一经过网关，按实际 API 返回 usage 计费，支持 prompt cache 折扣与墙钟记录。超出剩余预算的云端调用将被网关直接抛出 `BudgetExceededError` 拦截。
- **确定性验证器阶梯（L0 $\to$ L1 $\to$ L2）**：纯代码验证，区分廉价约束与昂贵评测，全流程在受限子进程隔离运行，杜绝在主进程中 `exec` 候选代码。
- **三类通用任务基准**：
  - 数学：单位正方形内 $n$ 个圆半径和最大化（可配置 $n$，报告原始和与相对初始解提升）。
  - 代码：批量余弦相似度 Top-K 性能优化，多轮交替计时中位数对比，消除噪声抖动。
  - 论文分析：段落级引文定位（记录文件名与段落号），事实题与综合题分列计分。
- **分档咨询与闭环验证**：默认启用 Hint 与 Solve 动作；Solve 直接产出候选送检；语法与格式错误仅触发本地重试。
- **决策账本与三线看板**：单动作单行 JSONL，分列记录云端花费、本地 GPU 时间与折算费用、端到端墙钟时间；一键生成包含"质量–云端花费"、"质量–总成本"、"质量–墙钟时间"三条曲线的自包含静态 HTML 看板。

---

## 快速上手（无需 GPU，无需网络，无需 API Key）

### 1. 安装依赖与环境配置

```bash
git clone <repo_url>
cd tierlab
pip install -e .
```

### 2. 运行自动化测试

```bash
pytest -v
```
所有测试均使用确定性 MockLLM 后端，在零网络、零 GPU、零付费 API 条件下 100% 通过。

### 3. 单次实验运行

```bash
# 数学任务，使用 TierLab 策略与 Mock 后端
tierlab run --task math --policy tierlab --backend mock --seed 0

# 代码优化任务，使用级联策略
tierlab run --task code --policy cascade --backend mock --seed 0

# 论文分析任务，使用全本地策略
tierlab run --task paper --policy all_local --backend mock --seed 0
```

### 4. 批量矩阵实验

```bash
tierlab batch configs/batch_experiment.yaml --output-dir runs/
```

### 5. 生成静态 HTML 看板

```bash
tierlab report runs/ --output report.html
```
生成单文件自包含 HTML（图表以 Base64 嵌入），双击即可在任何浏览器中离线查看三条性能曲线与账本回放。

### 6. 启动 API 服务

```bash
tierlab serve --host 127.0.0.1 --port 8000
```

---

## 策略说明

| 策略标识 | 说明 |
|---|---|
| `tierlab` | 完整框架：K 路本地并行投机 + 阶梯升级 + 经验证教训复用 |
| `all_cloud` | 公平对照基线：强模型每步出 1 个候选，按验证器反馈迭代，通过即停 |
| `all_local` | 纯本地基线：全程仅调用弱模型，零云端调用 |
| `cascade` | 传统级联基线：本地连续失败 $N$ 次后云端接管 |
| `static_split` | 云端单次前置规划，本地执行 |
| `no_advice_retry` | 归因消融：在达到咨询触发点时不调云端，给本地相同轮数重试机会 |

---

## 配置文件说明

所有模型名称、Base URL、单价均置于 `configs/` 目录下，切换真实模型只需修改 YAML 配置，代码库无需任何修改：

- `configs/models.yaml`：云端强模型、本地弱模型、可选小模型 API 地址、定价与单价占位。
- `configs/default.yaml`：默认步数、时间上限与预算参数。
- `configs/batch_experiment.yaml`：多任务 $\times$ 多策略 $\times$ 多种子批量实验矩阵。

## 第三轮审阅与提示词检查

实际请求样例、复现配置及六策略调用次数见
[docs/prompt_samples/README.md](docs/prompt_samples/README.md)，修复与回归测试映射见
[REVIEW.md](REVIEW.md)。

```bash
tierlab run --task paper --backend mock --seed 0 --dump-prompts
```

`--dump-prompts [目录]` 导出实际发给模型的消息和请求参数；不指定目录时，写入该次
运行目录下的 `prompts/`。CLI 与报告同时显示云端/本地调用次数和停止原因。

本地输出默认上限 2048 token，hint 默认 400；可用 `--local-max-tokens`、
`--hint-max-tokens` 或 controller YAML 配置。代码任务默认允许 NumPy，计时交叉
检查与最大加速比通过 `task_options.code` 配置。

`configs/models.yaml` 的云端单价现在为 `null` 占位。使用 real 后端前必须按所选
模型填写价格；缺失价格会在启动时直接报错。mock 使用合成计费，仅验证流程。

## 已知评测局限与硬件冒烟

代码任务允许 NumPy 时，模型可能一次就获得数十倍加速，后续提升空间有限。
正式策略对比应预先固定库约束，或另建朴素 NumPy 基线；当前冒烟仅验证服务和输出能否工作。

计时校验能拒绝直接改写 `time.perf_counter` 的候选，但普通 Python 子进程内的
输出协议仍可被篡改。例如改写 `json.dumps` 谎报较小耗时，可能绕过父进程容差
和 50 倍上限。当前结果不能作为任意恶意候选下的可信性能证明。

```bash
python smoke_vllm.py --model local-vllm-model --n 8
```

脚本使用导出的真实 local 提示词，保存原始回复、逐级验证结果、失败签名、思考泄漏、
截断次数，以及 K=1/2/4 的吞吐和 P95/最大延迟。输出默认在 `smoke_outputs/`，
可用 `--output-dir` 指定。API 单次失败不会丢失同批其他回复。

本地权重下载过慢时，可先使用硅基流动的同款基座 API 验证流程：

```bash
python smoke_vllm.py --provider siliconflow --model Qwen/Qwen3.6-35B-A3B \
  --n 8 --concurrency 2 --json-code --max-tokens 4096 --skip-throughput
tierlab run --task math --config configs/api_smoke.yaml --dump-prompts
```

先在环境变量中设置 `SILICONFLOW_API_KEY`。API 的费用、吞吐及精度不能作为本地
AWQ 推理的测量结果。请求格式、已发现的问题和复现配置见
[硅基流动冒烟说明](docs/SILICONFLOW_SMOKE.md)。
