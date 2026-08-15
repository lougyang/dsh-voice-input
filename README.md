# 🎤 dsh-voice-input — 系统级语音输入（DSH 插件）

在电脑上**任何能输入文字的地方**（浏览器、微信/QQ、Word、记事本、编辑器、搜索框……），按一个键说话，语音自动转成文字粘贴到当前光标处。**不只是 DSH 里能用**，是整个系统的能力。

- 完全本地运行：语音不出本机，无需联网、无 API 费用
- 本地小模型识别：Whisper `small`，中文效果好；有 N 卡自动走 GPU
- 轻量：模型懒加载，不用时不占显存/内存

## 功能

- 🎙 **语音转写**：按住 **F2** 说话 → 松开 → 文字粘贴到光标处
- ⏺ **纯录音**：按 **F3** 开始 / 再按一次结束，保存 WAV（只录声音、不转写）
- 📋 **历史记录**：每条识别/录音都记下来（内容 + 时长 + 时间），托盘「设置」里可查看、复制、清空
- ⚙️ **设置页面**：托盘图标右键 →「设置」，改热键 / 模式 / 模型 / 语言 / 设备 / 录音目录

## 快速开始

1. 确认电脑已装 **Python 3**（建议 3.10+），并有可用的麦克风。
2. 安装插件：
   ```sh
   npx -p @deepseek-ai/dsh dsh plugin --profile web add github:lougyang/dsh-voice-input
   ```
3. 重启 `web` profile。**挂载即自动启动**语音引擎（后台无窗口，托盘出麦克风图标）。
4. 在任意输入框里：**按住 F2 说话 → 松开 F2**，文字自动粘贴。

> 首次安装会自动创建 Python 环境并下载依赖（约 2.5GB）；首次按 F2 会自动下载模型（约 500MB）。都只发生一次。国内默认走清华 PyPI + hf-mirror 镜像。

## 停止 / 卸载

- 停止：托盘图标右键「退出」，或卸载插件（引擎随之停止）。
- 卸载：
  ```sh
  npx -p @deepseek-ai/dsh dsh plugin --profile web remove dsh-voice-input
  ```

## 配置

改法一：托盘「设置」页面（保存后即时生效，且不会随插件更新丢失）。

改法二：改 `cordis.patch.yml` 的 `config`（作为插件的默认配置）：

| 键 | 默认 | 说明 |
|---|---|---|
| `hotkey` | `f2` | 语音热键，如 `ctrl+alt+space` |
| `mode` | `hold` | `hold` 按住说话；`toggle` 按一下开/再按关 |
| `record_enabled` | `true` | 是否启用纯录音 |
| `record_hotkey` | `f3` | 录音热键 |
| `record_dir` | 空 | 录音保存目录；空则用 `%USERPROFILE%\Recordings` |
| `model` | `small` | `tiny`/`base`/`small`/`medium`/`large-v3` |
| `language` | `zh` | `zh`/`en`/`auto` |
| `device` | `auto` | `auto`(有N卡用GPU)/`cpu`/`cuda` |

## 常见问题

- **按热键没反应**：以管理员身份运行 DSH（普通权限无法向管理员窗口注入输入）。
- **没有识别到内容**：检查麦克风是否可用、Windows 麦克风权限、输入音量。
- **首次启动慢**：首次需建环境 + 下依赖 + 下模型，属正常；之后每次识别约 1~2 秒。

更完整的部署 / 排障说明（给 AI 代理看的）见 `AGENTS.md`。
