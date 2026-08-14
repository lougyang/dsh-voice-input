// dsh-voice-input — Host 侧插件
// apply() = 无窗口拉起内置语音引擎；dispose() = 干净停止。

import { spawn, spawnSync } from 'node:child_process'
import { existsSync, appendFileSync, openSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, resolve } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const engineDir = resolve(here, '..', 'engine')
const venvPythonw = join(engineDir, '.venv', 'Scripts', 'pythonw.exe')
const appPy = join(engineDir, 'app.py')
const bootstrapPy = join(engineDir, 'bootstrap.py')
const HOST_LOG = join(engineDir, 'host.log')

function flog(msg) {
  try { appendFileSync(HOST_LOG, new Date().toISOString() + ' ' + msg + '\n') } catch { /* noop */ }
}

export const name = 'voice-input'
export const inject = []

export function apply(ctx, config = {}) {
  flog('=== apply() 进入，config=' + JSON.stringify(config) + ' platform=' + process.platform)
  try {
    if (process.platform !== 'win32') {
      flog('非 Windows，跳过启动')
      return
    }

    let child = null

    const start = () => {
      let py = venvPythonw
      flog('venvPythonw=' + venvPythonw + ' exists=' + existsSync(venvPythonw))
      if (!existsSync(venvPythonw)) {
        flog('首次初始化（bootstrap）...')
        const sysPy = process.env.PYTHON ?? 'python'
        const r = spawnSync(sysPy, [bootstrapPy], { cwd: engineDir, stdio: 'ignore', shell: true })
        flog('bootstrap exit=' + r.status + ' venv exists now=' + existsSync(venvPythonw))
        if (r.status !== 0 || !existsSync(venvPythonw)) {
          flog('初始化失败')
          return
        }
        py = venvPythonw
      }

      const env = { ...process.env }
      if (config && typeof config === 'object') env.DSH_VOICE_CONFIG = JSON.stringify(config)

      let errFd = -1
      try { errFd = openSync(join(engineDir, 'engine-stderr.log'), 'a') } catch { /* noop */ }

      child = spawn(py, [appPy], {
        cwd: engineDir,
        windowsHide: true,
        stdio: errFd >= 0 ? ['ignore', 'ignore', errFd] : 'ignore',
        env,
      })
      child.on('error', (err) => flog('引擎启动失败(spawn error): ' + err.message))
      child.on('exit', (code, signal) => { child = null; flog('引擎已退出 code=' + code + ' signal=' + signal) })
      flog('引擎已 spawn, pid=' + child.pid)
    }

    start()

    ctx.on('dispose', () => {
      if (child) { try { child.kill() } catch { /* noop */ } child = null }
      flog('已停止（dispose）')
    })

    const webServer = ctx.get('webServer')
    flog('webServer=' + (webServer ? 'yes' : 'no'))
    if (webServer) {
      ctx.effect(() => webServer.register({
        kind: 'exact',
        path: '/shutdown',
        handler: (req, res) => {
          if (req.method !== 'POST') { res.statusCode = 405; res.end(); return }
          res.statusCode = 200
          res.setHeader('Content-Type', 'application/json; charset=utf-8')
          res.end('{"ok":true}')
          setTimeout(() => process.exit(0), 250)
        },
      }))
      flog('/shutdown 路由已注册')
    }
  } catch (e) {
    flog('!!! apply() 抛错: ' + (e && e.stack ? e.stack : String(e)))
  }
}
