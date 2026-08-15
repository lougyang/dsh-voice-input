// dsh-voice-input — Host 侧插件
// apply() = 无窗口拉起语音引擎（Python + faster-whisper）；dispose() = 连同子进程一起干净停止。
// 引擎是全局的：挂载后可在电脑任何程序里按热键语音输入，不只限于 DSH。

import { spawn, spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, join, resolve } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const engineDir = resolve(here, '..', 'engine')
const bootstrapPy = join(engineDir, 'bootstrap.py')

export const name = 'voice-input'
// 无需任何其它服务：apply() 一被激活就拉起引擎
export const inject = []

// 在 Windows 上探测可用的 Python 解释器（py 启动器优先，其次 python / python3）
function findPython() {
  const candidates = [
    { cmd: 'py', base: ['-3'] },
    { cmd: 'python', base: [] },
    { cmd: 'python3', base: [] },
  ]
  for (const c of candidates) {
    try {
      const r = spawnSync(c.cmd, [...c.base, '--version'], { stdio: 'ignore' })
      if (r.status === 0) return c
    } catch { /* try next */ }
  }
  return null
}

export function apply(ctx, config = {}) {
  if (process.platform !== 'win32') {
    ctx.logger?.warn?.('voice-input: 目前仅支持 Windows，已跳过启动')
    return
  }
  const log = (msg) => { try { ctx.logger?.info?.(msg) } catch { /* noop */ } }

  const py = findPython()
  if (!py) {
    log('voice-input: 未找到 Python，无法启动（请安装 Python 3 并加入 PATH）')
    return
  }

  const env = { ...process.env }
  if (config && typeof config === 'object') env.DSH_VOICE_CONFIG = JSON.stringify(config)

  // bootstrap.py：确保稳定 venv（%LOCALAPPDATA%\dsh-voice-input\venv）+ 装依赖，
  // 再以 venv 的 pythonw 无窗口拉起 app.py 并等待；宿主要停就整树终止。
  const child = spawn(py.cmd, [...py.base, bootstrapPy], {
    cwd: engineDir,
    windowsHide: true,
    stdio: 'ignore',
    env,
  })
  child.on('error', (err) => log('voice-input: 启动引擎失败 ' + err.message))
  child.on('exit', (code) => log('voice-input: 引擎已退出 code=' + (code ?? 'null')))

  log('voice-input: 语音输入已启动（热键 ' + (config.hotkey ?? 'f2') + '，按住说话、松开结束）')

  ctx.on('dispose', () => {
    try {
      // 整树终止：bootstrap.py 连同其拉起的 app.py 一起退出
      spawnSync('taskkill', ['/F', '/T', '/PID', String(child.pid)], { stdio: 'ignore' })
    } catch { /* noop */ }
    log('voice-input: 已停止')
  })
}
