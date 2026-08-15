# AGENTS.md — 本项目给 AI 代理的完整说明

> 本文档面向 AI 代理（Claude / GPT / DSH 等），不是给人看的产品文案。
> 目标：一个新克隆本仓库的 AI，只读本文档就能理解项目是什么、如何安装、
> 有哪些环境要求、会踩哪些坑、如何排障。请优先读本节再动手。

## 1. 项目是什么

`dsh-voice-input` 是一个 **DeepSeek Harness (DSH) 插件**，提供**系统级语音输入**：
- 挂载（install + 加载）后，Host 侧自动拉起一个 Python 语音引擎（后台无窗口、托盘常驻）。
- 引擎注册**全局热键**，在**任意 Windows 程序**里录音并转写，结果自动粘贴到当前光标处——与 DSH 的聊天界面无关，是系统级能力。
- 引擎还支持**纯录音**（只录声音存 WAV，不转写），以及**历史记录**（内容 + 时长 + 时间）。
- 转写用 faster-whisper 的 `small` 模型，**完全本地**，语音不出本机。

## 2. 架构 / 数据流

```
dsh plugin add github:lougyang/dsh-voice-input
        │  加载 web profile
        ▼
DSH Host 加载本插件 lib/index.js（inject=[]，无需其它服务）
        │  apply(ctx, config)
        │  ├─ findPython(): py -3 → python → python3（spawnSync --version 探测）
        │  └─ spawn(python, [bootstrap.py], {windowsHide, stdio:'ignore'})
        │        env.DSH_VOICE_CONFIG = JSON(config)   ← cordis.patch.yml 的 config
        ▼
engine/bootstrap.py（以系统 Python 运行，作为 app.py 的父进程）
        │  ensure_venv():
        │    venv 位置 = %LOCALAPPDATA%\dsh-voice-input\venv   ← 稳定，不被插件更新清空
        │    首次：python -m venv → pip install -r requirements.txt（清华镜像）
        │    用 requirements.txt 的 sha1 前 12 位写 .bootstrap-ok 标记，避免重复装
        └─ subprocess.Popen(pythonw.exe app.py) 并 wait() 等待
        ▼
engine/app.py（以 venv 的 pythonw 运行，无控制台窗口）
        │  单实例互斥量 DSH_VOICE_INPUT_SINGLETON（第二个实例自动退出）
        │  注册全局热键：F2 语音转写(hold) + F3 纯录音(toggle)
        │  语音：sounddevice 录音 → faster-whisper 转写 → pyperclip.copy + keyboard.send('ctrl+v')
        │  录音：sounddevice 录音 → 存 WAV 到 %USERPROFILE%\Recordings（可配置）
        │  历史：每条写进 %LOCALAPPDATA%\dsh-voice-input\history.json
        │  托盘（pystray）：复制最近识别 / 设置… / 退出
        └─ 模型懒加载：首次按热键才下载/加载 Whisper small（hf-mirror）
```

停止：DSH 卸载/关闭本插件时 `ctx.on('dispose')` 执行
`taskkill /F /T /PID <bootstrap_pid>`，整树终止 bootstrap.py + app.py。

## 3. 稳定数据目录（关键）

所有跨更新、跨重启需要保留的东西都在 `%LOCALAPPDATA%\dsh-voice-input\`：

| 路径 | 用途 |
|---|---|
| `venv\` | Python 虚拟环境（放这里是为了不被 `dsh plugin update` 清空） |
| `.bootstrap-ok` | 记录已装依赖对应的 requirements.txt 哈希 |
| `bootstrap.log` | bootstrap 引导日志（建 venv / 装依赖） |
| `voice_input.log` | 引擎运行日志（启动、识别、报错） |
| `config.json` | 用户在设置页保存的配置（最高优先级） |
| `history.json` | 历史记录（文本/录音 + 时长 + 时间） |

录音文件默认存在 `%USERPROFILE%\Recordings\`（可在设置页改目录）。

## 4. 环境要求（安装前提）

- **操作系统**：Windows（仅支持 Windows；`lib/index.js` 在非 win32 直接跳过）。
- **Python 3**：必须已安装且可被 `py -3` 或 `python` 找到。建议 3.10 ~ 3.13。
  - 探测顺序：`py -3` → `python` → `python3`（`spawnSync --version`，status==0 即命中）。
  - 找不到会记日志并跳过启动，插件不会崩 DSH。
- **麦克风**：需要可用麦克风；否则能启动但识别为空。
- **GPU（可选）**：有 NVIDIA 显卡自动用 CUDA（float16）；无则回退 CPU（int8）。
  - requirements.txt 里带了 nvidia-cublas/cudnn/cuda-runtime cu12；纯 CPU 可删这 3 行。
- **磁盘/内存**：依赖约 2.5GB（含 GPU 库）；模型 small 约 500MB；运行期显存约 1GB（GPU）或内存约 1.5GB（CPU）。

## 5. 网络 / 镜像（中国大陆重点）

- pip 用清华镜像：`https://pypi.tuna.tsinghua.edu.cn/simple`（写死在 bootstrap.py）。
- 模型用 hf-mirror：`engine/config.json` 的 `hf_endpoint=https://hf-mirror.com`，app.py 会设 `HF_ENDPOINT` 环境变量。
- 海外用户：把 `hf_endpoint` 改成 `""` 用官方 huggingface.co。
- 若公司/家庭网络需要代理，需在启动 DSH 的进程环境里配 `HTTP_PROXY`/`HTTPS_PROXY`。

## 6. 配置优先级

app.py 的 `load_config()` 合并顺序（后者覆盖前者，越靠后优先级越高）：

1. 代码内置 `DEFAULTS`（hotkey=f2, mode=hold, model=small, ...）
2. `engine/config.json`（随包默认）
3. 环境变量 `DSH_VOICE_CONFIG`（插件 Host 从 cordis.patch.yml 的 config 序列化注入）
4. `%LOCALAPPDATA%\dsh-voice-input\config.json`（用户在**设置页**保存的本地配置）——最高

所以：改插件默认 → 改 `cordis.patch.yml` 的 `config`；
改单机覆盖 → 用户在托盘「设置」页改（写进 LOCALAPPDATA，稳定）。

## 7. 已知坑 / 排障（重点）

### 7.1 venv 会被 dsh plugin update 清空 —— 已解决
`dsh plugin update` 会重新克隆插件目录（`engine/` 会被重置），所以 venv **绝对不能放插件目录里**。
本项目 venv 放在 `%LOCALAPPDATA%\dsh-voice-input\venv`，更新插件不影响它。
- 手动强制重装环境：删除 `%LOCALAPPDATA%\dsh-voice-input\` 整个目录后重启插件。

### 7.2 首次启动慢 / 看起来没反应
首次要：建 venv + 装依赖（分钟级）+ 首次按热键下模型（约 500MB）。
引导进度/错误在 `%LOCALAPPDATA%\dsh-voice-input\bootstrap.log`。
引擎运行日志在 `%LOCALAPPDATA%\dsh-voice-input\voice_input.log`。
- 判据：`bootstrap.log` 出现 "dependencies installed" 即依赖就绪；`voice_input.log` 出现 "语音输入已启动" 即热键已注册。

### 7.3 没有识别到内容（常见）
几乎都是麦克风问题，不是代码问题：
- Windows「设置→系统→声音→输入」确认默认麦克风正确、音量>0、未静音。
- 隐私设置「麦克风」允许桌面应用访问。
- 靠近说话、环境安静。
`voice_input.log` 会留 "没有识别到内容"。

### 7.4 CUDA / cublas64_12.dll 找不到
app.py 的 `_setup_cuda_dll_dirs()` 会把 nvidia 包的 bin 目录加进 DLL 搜索路径 + PATH。
若仍报缺 DLL：确认 requirements.txt 里 3 个 nvidia 包装上了；或直接在设置页把设备改成 `cpu`。

### 7.5 无法向某些程序注入输入（管理员窗口）
全局键盘注入（keyboard 库）对以管理员运行的目标窗口需要本进程也是管理员。
- 解决办法：以管理员身份运行 DSH。
- 或在 `engine/config.json` 设 `insert_mode:"clipboard"`（只进剪贴板，手动 Ctrl+V）。注意现默认是 `paste`（自动 Ctrl+V）。

### 7.6 单实例
app.py 用命名互斥量 `DSH_VOICE_INPUT_SINGLETON` 保证单实例。
如果同时手动跑了独立版语音工具，插件拉起的引擎会因互斥量自动退出（反之亦然），不会重复。

### 7.7 停止 / 孤儿进程
卸载/关闭插件 → taskkill /T 整树终止。若 DSH 崩溃导致孤儿进程，托盘右键「退出」可手动停。

### 7.8 设置页
设置页是 tkinter 窗口，从托盘「设置…」打开（主线程轮询 `_open_settings` 标志，避免跨线程动 tk）。
保存 = 写 LOCALAPPDATA/config.json + 重新注册热键（`keyboard.unhook_all()` 后重挂）。
模型/语言/设备改动下次识别生效（模型懒加载，改模型不会立刻重新加载）。

## 8. 目录结构

```
dsh-voice-input/
  package.json         # name/version/author/repo + dsh.bundle.patch
  cordis.patch.yml     # 插入 Host 行（id: voice-input, config: 热键/模型/录音等）
  lib/index.js         # Host：找 Python → spawn bootstrap.py → dispose 时 taskkill /T
  engine/
    bootstrap.py       # 稳定 venv 引导（LOCALAPPDATA）+ 拉起 app.py
    app.py             # 引擎：热键/录音/转写/粘贴/历史/托盘/设置页
    config.json        # 随包默认配置
    requirements.txt   # faster-whisper, sounddevice, keyboard, pyperclip, numpy + nvidia cu12
  README.md            # 给人看的简介
  AGENTS.md            # 本文档（给 AI）
```

## 9. 验证清单（装完怎么确认成功）

1. `bootstrap.log` 出现 "dependencies installed" → 依赖装好。
2. `voice_input.log` 出现 "语音输入已启动" → 热键注册成功。
3. 打开任意文本输入框，按住 F2 说一句话，松开，看是否粘贴文字。
4. 托盘区出现麦克风图标；右键「设置…」能打开设置窗口、能看到历史记录。
5. 按 F3 一次开始录音、再按一次停止，`%USERPROFILE%\Recordings` 出现 WAV。

## 10. 不要做的事

- 不要把 Whisper 模型权重（约 500MB）提交进 git：GitHub 单文件限 100MB，且会严重拖慢 clone；模型由 app.py 运行时从 hf-mirror 自动下载。
- 不要把 venv 放回插件目录（engine/.venv）：会被 `dsh plugin update` 清空，见 7.1。
- 不要把敏感信息（API key 等）写进配置；本项目纯本地，无需任何 key。
- 不要把「关闭服务按钮 / 桌面图标」等个人本地小工具塞进本仓库——那是作者自用的，单独放本地仓库，不发布。
