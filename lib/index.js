// dsh-shutdown-button — Host 侧插件
// 注册 /shutdown 接口，供网页右上角「关闭服务」按钮调用；
// 确认后关闭 DeepSeek 服务进程。

// 稳定插件名，须与 cordis.patch.yml 里的 id 一致
export const name = 'shutdown-button'
// 声明 webServer 为硬依赖：等它就绪后再激活，避免 apply 跑在 webServer 之前导致路由注册失败
export const inject = ['webServer']

export function apply(ctx) {
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
}
