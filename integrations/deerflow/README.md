# VELA / DeerFlow 前端适配

原生聊天协同时间线已实现，安装和验收材料见 [DeerFlow 协同展示](../../docs/DEERFLOW_COLLABORATION.md)。

独立研究引擎通过 HTTP 提供任务、进度和账本，DeerFlow 只承载页面及可选对话工具。前端不持有模型密钥，也不把研究控制器嵌进 DeerFlow agent 循环。

## 先打开可独立运行的工作台

在本项目根目录执行：

```bash
pip install -e ".[dev]"
tierlab serve --host 127.0.0.1 --port 8000
```

访问 http://127.0.0.1:8000 。点击“新建研究实验”，默认 mock，无需模型密钥。工作台支持三类任务、六种策略、并行宽度、预算/时间/轮数、生成参数、历史记录、验证反馈、开发得分曲线、决策详情、JSONL 导出与最终报告。移动端可使用。

`real` 使用服务端配置和环境变量；选择 SiliconFlow 配置时，小模型后端应设为 `cloud_small`，并在启动服务的环境中设置 `SILICONFLOW_API_KEY`。选择本地后端需已启动 vLLM。默认模型配置单价是占位符，未配置会显示失败原因。配置路径不由浏览器任意指定。

## 安装到 DeerFlow 2.x

需要一个已有的 DeerFlow 2.x checkout（Next.js App Router、`frontend/src/app/workspace/layout.tsx`）。从本项目根目录执行：

```bash
python scripts/install_deerflow_frontend.py /absolute/path/to/deer-flow
```

脚本安装：

- `/workspace/vela`：复用 DeerFlow workspace 的登录/布局外壳，内嵌完整 VELA 工作台。
- `/vela-proxy/[[...path]]`：经 DeerFlow 登录检查后的服务端同源代理；固定上游和可访问路由，浏览器不需要 CORS 配置。
- `backend/vela_integration/`：HTTP 工具客户端和 LangChain tool 注册。
- `skills/custom/vela-research/SKILL.md`：对话操作说明。

脚本不修改已有配置或导航，不覆盖不同内容的同名文件。重启 DeerFlow 后直接打开 `/workspace/vela`；可在自身导航中添加指向该地址的链接。

在 DeerFlow 前端进程环境中设置（无须 `NEXT_PUBLIC_` 前缀）：

```dotenv
TIERLAB_SERVER_URL=http://127.0.0.1:8000
```

如前端在容器内，地址应指向容器可访问的研究服务，而不是容器自身的 localhost。该适配面向单个操作者的研究空间；VELA 服务本身没有用户/租户隔离，保持本地或私有网络访问。代理沿用 DeerFlow 当前 `getServerSideUser` 接口，未登录返回 401。

## 可选：对话启动与查询

同一个环境变量也需设置在 DeerFlow 后端进程中。合并以下项到其 `config.yaml` 的已有列表中（不要替换原列表）：

```yaml
tool_groups:
  - name: vela_research
tools:
  - name: start_research
    group: vela_research
    use: vela_integration.tools:start_research_tool
  - name: get_research_status
    group: vela_research
    use: vela_integration.tools:get_research_status_tool
  - name: get_research_report
    group: vela_research
    use: vela_integration.tools:get_research_report_tool
```

工具只提交后台任务或查询状态。对话层模型的 token 不属于研究引擎账本，需由 DeerFlow 另行统计。纯工作台使用不要求对话模型或注册这些工具。

## 数据与边界

`GET /runs` 返回历史，`GET /runs/{id}` 返回状态与摘要，`GET /runs/{id}/ledger?offset=0&limit=200` 返回分页决策，`GET /runs/{id}/report` 返回 HTML 报告。每次运行保存请求配置、状态、summary 和账本。重启后未完成任务标为 interrupted，不误报完成。实验串行执行，HTTP 查询在运行期间保持响应。

费用沿用引擎记账：小模型走 API 时同样计入云端；GPU 忙时费用与墙钟租赁折算分别展示。mock 必须单独解释。数学、代码、论文得分单位不同，前端不强行转成同一个百分数。开发曲线排除最终评估，最终得分单列。

本地工作台和 DeerFlow 聊天 Agent 已分别通过浏览器检查。2026-11-14 的本机演示使用真实对话模型调用研究工具，并在原生 Artifacts 中打开报告；研究执行使用 mock，不构成真实研究质量或成本对比。工具与产物记录见 [deerflow-agent-demo.json](../../docs/deerflow-agent-demo.json)，界面见 [截图](../../docs/deerflow-agent-demo.png)。本机免登录模式不覆盖正式部署的登录流程验证。

实现依据（核对于 2026-11-14）：[DeerFlow workspace 布局](https://github.com/bytedance/deer-flow/blob/main/frontend/src/app/workspace/layout.tsx)、[工具配置](https://github.com/bytedance/deer-flow/blob/main/backend/docs/CONFIGURATION.md)、[Next.js Route Handlers](https://nextjs.org/docs/app/api-reference/file-conventions/route)、[FastAPI 静态文件](https://fastapi.tiangolo.com/tutorial/static-files/)。


## Agent 演示与防护软件拦截

### 从 GitHub 准备构建环境

需要 Docker 的 Linux 容器引擎、Python 3.11+ 和已安装的项目依赖。构建准备脚本会
下载并校验固定提交的源归档，组装源码、配置、数据与集成文件；不在宿主机解压或
执行上游后端 Python。首次构建需要联网下载依赖，但不会调用付费模型。

```powershell
python scripts/prepare_deerflow_demo.py --build
```

如果已有本机验证过的源归档，可以复用：

```powershell
python scripts/prepare_deerflow_demo.py --archive runs/deerflow-container/source.zip --build
```

不加 `--build` 时只准备上下文并打印构建命令。归档 SHA-256 固定为
`0fd319b5bda948f93ac32ea026ff519dad0fdc0ca213b6593e98eb7c1f779a0e`，与此前验收镜像的来源一致。

前端需要固定版本的 DeerFlow checkout，以及 Node/pnpm 依赖。新的 checkout 可按下列
命令准备；其后端运行只使用上述 Docker 镜像，不在 Windows 上原生执行：

```powershell
git clone https://github.com/bytedance/deer-flow.git runs/deer-flow
git -C runs/deer-flow checkout 3e2489429ff3061489537d7303240fef3f199f83
python scripts/install_deerflow_frontend.py runs/deer-flow
python scripts/install_deerflow_collaboration.py runs/deer-flow
```

在 `runs/deer-flow/frontend` 中执行 `pnpm install --frozen-lockfile`（pnpm 版本遵循
该 checkout 的 `packageManager`），然后按下文设置前端环境和启动服务。当前本机的
既有 checkout 已安装过适配，不需要再次克隆。若防护软件处理了宿主机后端文件，
不需要恢复它来运行容器后端。

新的 checkout 对应启动命令为 `python scripts/run_deerflow_agent_demo.py runs/deer-flow`，
从本项目根目录执行；下文下载目录的命令是当前验收电脑的既有路径示例。

真正的演示入口是 DeerFlow 聊天页，研究看板只是辅助页面。Agent 应实际调用
`start_research` → `get_research_status(wait_seconds=90)` → `get_research_report` →
`present_files(filepaths=[artifact_path])`，最后在原生 Artifacts 面板展示报告。

2026-11-14，Windows 火绒在本机原生 Gateway 导入时删除了上游
`skills/skillscan/orchestrator.py`，因此原生启动未成功。没有恢复该文件或修改白名单。
归档内文件与提交 `3e2489429ff3061489537d7303240fef3f199f83` 的 Git blob 一致：
`f19531114a9fda95ac9d46e48fe897ba0cba16d3`；SHA-256：
`c618364e2537a6c8094b57ee178130373cc25688a28bda13f9ee0ad9521d5e33`。
上游文件为静态安全扫描器，包含反向 Shell 的识别规则；这些证据不等同于防病毒厂商的误报确认。

当前启动脚本改用隔离容器，不再执行 Windows 上被删除的模块：

- Gateway 与研究引擎均以非 root 用户运行，不挂载用户目录或 Docker socket。
- 只读根文件系统；独立命名卷保存该演示自己的会话与报告。
- 去掉 Linux capabilities，禁止新增权限，并限制内存、CPU 和进程数。
- Gateway 仅发布 `127.0.0.1:8001`，研究引擎发布 `127.0.0.1:18766`，前端监听 `127.0.0.1:3000`。
- API key 通过环境变量传入，不写入镜像或源码；研究执行与对话层的用量分开。

镜像构建使用 `Dockerfile.demo`，上下文包含经过哈希核对的 `source.zip`、
`integration/`、研究源码与配置目录 `research/` 和不含明文密钥的 `config.yaml`。镜像标签为
`vela-deerflow-demo:3e248942`。准备好镜像后，在本项目根目录运行：

```bash
python scripts/run_deerflow_agent_demo.py runs/deer-flow-download/deer-flow-3e2489429ff3061489537d7303240fef3f199f83
```

脚本通过隐藏输入读取 API key，在两个容器内分别启动研究引擎和 Gateway。
研究引擎容器内为 8766，宿主机映射为 18766（可用 `--engine-port` 修改）。
前端进程环境设置 `TIERLAB_SERVER_URL=http://127.0.0.1:18766`、
`DEER_FLOW_INTERNAL_GATEWAY_BASE_URL=http://127.0.0.1:8001`、
`DEER_FLOW_AUTH_DISABLED=1` 和
`DEER_FLOW_TRUSTED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000`，
随后从 DeerFlow `frontend/` 目录执行 `node scripts/dev.mjs --port 3000`。
打开 http://127.0.0.1:3000/workspace/chats/new 。本地单人演示使用上游提供的免登录模式，
服务保持回环监听。需要停止后端时执行
`docker stop vela-agent-gateway vela-research-engine`。

首次可以输入：

> 请在单位正方形内放置 26 个互不重叠的圆，尽量增大半径之和。请实际搜索并验证，最后给我优化前后的指标和实验报告。

面向评委时只输入研究任务，不需要写工具名或 API 参数。启动脚本将
`lead_prompt.md` 作为上游 `lead_prompt_overlay.append` 注入，并以只读方式挂载
生成配置、本 Skill 和工具客户端；研究源码也以只读方式挂载。普通受支持任务默认使用真实模型，研究限制为 6 步、
240 秒、0.5 元预算；对话 Agent 自身的用量另计。需要纯流程演示时明确说
“这次使用模拟执行”。修改指令后重新创建 Gateway 生效，前端不需要重启。

另一个任务示例：

> 请优化内置的批量余弦相似度 Top-K 检索算法。在保持结果正确的前提下提高运行速度，用自动测试和独立最终评测验证，并给我性能对比和实验报告。

当前工具只接收固定任务标识，无法把任意用户代码、新论文或修改后的数学约束
传入研究引擎。Agent 必须说明这一边界，不能把不匹配的请求冒充为已执行任务。
大小模型当前都走 API，不能宣称已在本地 GPU 上执行。

本机已验证上述工具链和 HTML 报告预览。示例结果为数学得分 0.7799、3 次 mock 本地调用、0 次研究云端调用；这些合成结果只用于流程演示。对话 Agent 自身调用真实模型，其费用不包含在研究引擎的零云费用中。
