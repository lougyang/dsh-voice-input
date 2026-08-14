# 🎤 dsh-voice-input — 系统级语音输入插件（DSH）

全局热键录音 → 本地 Whisper 转写 → 自动注入到当前光标所在的**任意应用**（浏览器、微信、Word、编辑器、搜索框……）。纯本地运行，语音不出本机、无需联网、无 API 费用。

## 服务信息（安装前请看）

| 项目 | 值 |
|---|---|
| 识别模型 | Whisper `small`（faster-whisper，约 2.4 亿参数） |
| 运行期资源 | **显存约 1GB**（有 N 卡时）/ **内存约 1.5GB**；空闲不加载模型、不占显存 |
| 首次下载 | 模型约 500MB（自动，走 hf-mirror 国内镜像）+ 依赖约 2.5GB（含 GPU 库） |
| 语言 | 中文（默认）、英文，可自动检测 |
| 热键 | `F9`（按一次开始、再按一次结束；可改） |
| 实时显示 | 分块滚动（约每 1.6s 刷新一段），悬浮框跟随光标所在屏 |
| 剪贴板 | 识别结果**始终留在剪贴板**，托盘菜单可复制最近识别 |
| 系统要求 | Windows；已安装 Python 3.13；有麦克风 |

## 安装

```sh
npx -p @deepseek-ai/dsh dsh plugin --profile web add github:lougyang/dsh-voice-input
```

装完重启 `web` profile 即生效。挂载即启动、卸载即停止（`dsh plugin --profile web remove dsh-voice-input`）。

> 首次启动会自动：创建虚拟环境 → 安装依赖 → 下载 `small` 模型，需几分钟，只发生一次。

## 使用

1. 在任意输入框里按 **F9** 开始说话（屏幕出现「正在聆听…」）。
2. 说话时文字会**一段一段**实时显示在悬浮框里。
3. 再按 **F9** 结束 → 自动识别并粘贴到光标处，文字同时留在剪贴板。

## 配置（在 `cordis.patch.yml` 的 `config` 里改）

| 键 | 默认 | 说明 |
|---|---|---|
| `hotkey` | `f9` | 全局热键，如 `ctrl+alt+space` |
| `mode` | `toggle` | `toggle` 按一下开/再按关；`hold` 按住说话 |
| `model` | `small` | `tiny`/`base`/`small`/`medium`/`large-v3` |
| `language` | `zh` | `zh`/`en`/`null`(自动) |
| `device` | `auto` | `auto`(有 N 卡用 GPU)/`cpu`/`cuda` |
| `previewInterval` | `1.6` | 实时显示刷新间隔（秒） |

## 目录结构

```
dsh-voice-input/
  package.json         # dsh.bundle 声明 + 元数据
  cordis.patch.yml     # 插入 Host 插件行
  lib/index.js         # Host：apply() 拉起引擎 / dispose() 停止
  engine/              # Python 引擎（faster-whisper）
    app.py             # 主程序：热键/录音/分块显示/托盘/剪贴板/占用
    bootstrap.py       # 首次运行：建 venv + 装依赖 + 下模型
    requirements.txt
    config.json
```
