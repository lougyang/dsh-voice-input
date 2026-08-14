// dsh-voice-input — Host 侧插件（仅提供「关闭服务」按钮的后端接口）
// 语音输入已独立为桌面图标，这里只负责 /shutdown 路由。

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
// 声明 webServer 为硬依赖：等它就绪后再激活，避免 apply 跑在 webServer 之前导致路由注册失败
export const inject = ['webServer']

export function apply(ctx, config = {}) {
  flog('=== apply() 进入 platform=' + process.platform)
  try {
    ctx.effect(() => ctx.webServer.register({
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
  } catch (e) {
    flog('!!! apply() 抛错: ' + (e && e.stack ? e.stack : String(e)))
  }
}
