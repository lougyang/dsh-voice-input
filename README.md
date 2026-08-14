# ⏻ dsh-shutdown-button — DSH 网页「关闭服务」按钮插件

在 DSH 网页右上角加一个「关闭服务」按钮。点击后弹出确认框，确认才真正关闭 DeepSeek 服务进程，避免误触直接断开。

## 功能

- 右上角「关闭服务」按钮（红色）
- 点击 → 浏览器弹确认框「确定要关闭 DeepSeek 服务吗？」
- 确认 → 调用后端 `/shutdown` 接口，关闭 DSH 进程；取消 → 无动作

## 安装

```sh
npx -p @deepseek-ai/dsh dsh plugin --profile web add github:lougyang/dsh-shutdown-button
```

装完重启 `web` profile 即生效。

## 卸载

```sh
npx -p @deepseek-ai/dsh dsh plugin --profile web remove dsh-shutdown-button
```

## 目录结构

```
dsh-shutdown-button/
  package.json         # dsh.bundle 声明 + 元数据
  cordis.patch.yml     # 插入 Host 插件行
  lib/index.js         # Host：注册 /shutdown 接口
  lib/client.js        # 网页：右上角「关闭服务」按钮
```
