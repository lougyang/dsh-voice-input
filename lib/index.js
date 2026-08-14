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

  const log = (msg) => { try { ctx.logger?.info?.(msg) } catch { /* noop */ } }
  let child = null

  const start = () => {
    // 直接用 venv 里的 pythonw 全路径（不依赖系统 PATH，桌面启动的新进程也能找到）
    let py = venvPythonw
    if (!existsSync(venvPythonw)) {
      log('voice-input: 首次初始化（创建 venv + 安装依赖 + 下载模型），请稍候…')
      const sysPy = process.env.PYTHON ?? 'python'
      const r = spawnSync(sysPy, [bootstrapPy], {
        cwd: engineDir,
        stdio: 'ignore',
        shell: process.platform === 'win32',
      })
      if (r.status !== 0 || !existsSync(venvPythonw)) {
        log('voice-input: 初始化失败——请确认已安装 Python 3.13')
        return
      }
      py = venvPythonw
    }

    const env = { ...process.env }
    if (config && typeof config === 'object') {
      env.DSH_VOICE_CONFIG = JSON.stringify(config)
    }

    child = spawn(py, [appPy], {
      cwd: engineDir,
      windowsHide: true,
      stdio: 'ignore',
      env,
    })
    child.on('error', (err) => log('voice-input: 启动引擎失败 ' + err.message))
    child.on('exit', (code) => { child = null; log('voice-input: 引擎已退出 code=' + code) })
    log('voice-input: 语音输入已启动（热键 ' + (config.hotkey ?? 'f9') + '）')
  }

  start()

  ctx.on('dispose', () => {
    if (child) {
      try { child.kill() } catch { /* noop */ }
      child = null
    }
    log('voice-input: 已停止')
  })

  // --- 关闭服务按钮的后端接口（仅 web 环境存在 webServer）---
  const webServer = ctx.get('webServer')
  if (webServer) {
    ctx.effect(() => webServer.register({
      kind: 'exact',
      path: '/shutdown',
      handler: (req, res) => {
        if (req.method !== 'POST') {
          res.statusCode = 405
          res.end()
          return
        }
        res.statusCode = 200
        res.setHeader('Content-Type', 'application/json; charset=utf-8')
        res.end('{"ok":true}')
        setTimeout(() => process.exit(0), 250)
      },
    }))
  }
}
