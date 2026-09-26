# AI_SETUP_PROMPT: 智能代理全自动部署与配置状态机协议

> **适用对象**: Codex CLI (`codex exec`), Antigravity (`agy`), Claude Code (`claude -p`) 或其他终端 AI 智能体。  
> **设计准则**: 严格区分本质复杂度（向用户索要必要凭证）与附带复杂度（系统自动化装配）。禁止现场即兴编写系统级服务脚本，所有系统服务挂载强制调用 `setup_wizard.py` 内部模板插值。

---

## 角色指令与执行约束 (System Invariants)

你是本工程的「自治交付工程师（Autonomous Delivery Engineer）」。你的职责是帮助人类用户在当前机器上**以最小认知摩擦**完整跑通 `anki-context-miner`。

### 🚨 绝对禁止项 (Negative Invariants)
1. **禁止污染全局 Python**：必须且仅能在本地虚拟环境 `.venv` 中安装依赖。
2. **禁止即兴编写系统服务**：严禁自行手写 `launchd.plist`、Windows 注册表修改或任务计划 XML，必须直接调用项目内置的 `python setup_wizard.py --install-service`。
3. **禁止臆测凭证**：Telegram Bot Token、Admin ID 属于物理授权凭证，必须且只能向人类用户询问。

---

## 状态机执行流 (Deterministic State Machine)

请按顺序依次执行以下 5 个步骤，步骤未达标不得越级跳转：

### 阶段 1: 物理环境诊断 (Phase 1: Environment Diagnosis)
在终端运行：
```bash
python3 -c "import sys; print(sys.version)" || python -c "import sys; print(sys.version)"
ffmpeg -version
```
- 若 Python < 3.9：指导用户安装 Python 3.10+。
- 若缺少 FFmpeg：
  - macOS: 主动调用 `brew install ffmpeg` 安装。
  - Windows: 主动调用 `winget install Gyan.FFmpeg` 安装。

### 阶段 2: 隔离沙盒初始化 (Phase 2: Sandbox Setup)
在终端运行：
```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Windows
python -m venv .venv
call .venv\Scripts\activate.bat
pip install -r requirements.txt
```

### 阶段 3: 人机边界协商 (Phase 3: Essential Credentials Ingestion)
向人类用户发出明确、精炼的询问（仅此一次，收集完即止）：
1. **Telegram Bot Token**:（告知用户从 Telegram 找 `@BotFather` 发送 `/newbot` 获取）
2. **Telegram Admin ID**:（告知用户在 Telegram 找 `@userinfobot` 获取自己的纯数字 ID）
3. **LLM 模型选择**:（告知用户支持选择已登录的本地 CLI `codex` / `agy` / `claude`，或输入 `gemini` / `deepseek` / `openai` 的 API Key）

收到回复后，将信息精准写入或覆盖当前目录下的 `config.json`（可参考 `config.example.json` 结构）。

### 阶段 4: 物理连通性探针核验 (Phase 4: Physical Verification Gate)
在终端运行物理探针：
```bash
# macOS
.venv/bin/python3 setup_wizard.py --check-only

# Windows
.venv\Scripts\python.exe setup_wizard.py --check-only
```
- 若 AnkiConnect 显示 `[FAIL]`：提醒用户“请在后台打开 Anki 桌面端，并确保已安装 AnkiConnect 插件（代码 2055492159）”。
- 若 Telegram 显示 `[FAIL]`：检查网络或提示用户配置代理。

### 阶段 5: 模板化服务挂载 (Phase 5: Silent Daemon Mounting)
在连通性探测全部 PASS 后，执行模板化系统服务注入：
```bash
# macOS
.venv/bin/python3 setup_wizard.py --install-service

# Windows
.venv\Scripts\python.exe setup_wizard.py --install-service
```

### 阶段 6: 交付报告 (Phase 6: Completion Handover)
向用户输出结构化报告：
- 确认后台服务已静默拉起
- 提示用户向自己的 Telegram Bot 发送 `/status` 或 `/help` 进行首次测试
- 说明若需停止或卸载，可运行对应脚本或命令。
