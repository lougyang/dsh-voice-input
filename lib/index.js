// dsh-voice-input — Host 侧插件（现仅提供「关闭服务」按钮的后端接口）
// 语音输入已独立为桌面图标，这里不再拉起引擎。

import { appendFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, resolve } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const engineDir = resolve(here, '..', 'engine')
const HOST_LOG = join(engineDir, 'host.log')

function flog(msg) {
  try { appendFileSync(HOST_LOG, new Date().toISOString() + ' ' + msg + '\n') } catch { /* noop */ }
}

export const name = 'voice-input'
export const inject = []

export function apply(ctx, config = {}) {
  flog('=== apply() 进入 platform=' + process.platform)
  try {
    if (process.platform !== 'win32') { flog('非 Windows'); return }

    const webServer = ctx.get('webServer')
    flog('webServer 取值=' + (webServer ? 'yes' : 'undefined'))

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
