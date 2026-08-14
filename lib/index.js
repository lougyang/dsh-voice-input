// dsh-voice-input — Host 侧插件
// apply() = 无窗口拉起内置语音引擎；dispose() = 干净停止。
// 引擎是 Python（faster-whisper），首次使用自动建 venv + 装依赖 + 下模型。

import { spawn, spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, resolve } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const engineDir = resolve(here, '..', 'engine')
const venvPythonw = join(engineDir, '.venv', 'Scripts', 'pythonw.exe')
const appPy = join(engineDir, 'app.py')
const bootstrapPy = join(engineDir, 'bootstrap.py')

export const name = 'voice-input'
export const inject = []

export function apply(ctx, config = {}) {
  if (process.platform !== 'win32') {
    ctx.logger?.warn?.('voice-input: 目前仅支持 Windows，跳过启动')
    return
  }

  const log = (msg) => ctx.logger?.info?.(msg)

  const start = () => {
    let py = 'pythonw'
    if (!existsSync(venvPythonw)) {
      // 首次运行：用系统 python 建 venv + 装依赖 + 预下载模型（一次性，可能几分钟）
      log('voice-input: 首次初始化（创建 venv + 安装 faster-whisper + 下载模型），请稍候…')
      const sysPy = process.env.PYTHON ?? 'python'
      const r = spawnSync(sysPy, [bootstrapPy], {
        cwd: engineDir,
        stdio: 'ignore',
        shell: process.platform === 'win32',
      })
      if (r.status !== 0 || !existsSync(venvPythonw)) {
        ctx.logger?.error?.('voice-input: 初始化失败——请确认已安装 Python 3.13')
        return
      }
      py = venvPythonw
    }

    const env = { ...process.env }
    if (config && typeof config === 'object') {
      env.DSH_VOICE_CONFIG = JSON.stringify(config)
    }

    const child = spawn(py, [appPy], {
      cwd: engineDir,
      windowsHide: true,
      stdio: 'ignore',
      env,
    })
    child.on('exit', () => { this._child = null })
    this._child = child
    log('voice-input: 语音输入已启动（热键 ' + (config.hotkey ?? 'f9') + '）')
  }

  start()

  ctx.on('dispose', () => {
    if (this._child) {
      try { this._child.kill() } catch { /* noop */ }
      this._child = null
    }
    log('voice-input: 已停止')
  })
}
