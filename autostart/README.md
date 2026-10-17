# Zotero PDF2zh 开机自动启动（无需终端）

## 这是什么

原来每次用翻译功能都要先开一个终端跑：

```powershell
cd E:\Zotero\PDF2Zh\server
uv run --python 3.12 --with-requirements requirements.txt server.py
```

现在改成：**只要打开 Zotero，翻译服务就会自动在后台启动**，不用再碰终端。
关掉 Zotero 两分钟后，服务会自动退出，不占内存。

## 它是怎么工作的

```
登录 Windows
   └─ 计划任务 / 启动文件夹  →  守护脚本（隐藏窗口，常驻）
                                   │
                                   ├─ 每 5 秒检查一次 Zotero 是否在运行
                                   │
                                   ├─ Zotero 一启动 → 后台拉起 server.py（无窗口）
                                   │                 并轮询 /health 直到就绪
                                   │
                                   ├─ server.py 意外崩溃 → 自动重新拉起
                                   │
                                   └─ Zotero 退出满 2 分钟 → 停掉 server.py
```

另外，计划任务每 **5 分钟** 自检一次：万一守护进程被误杀，
最多 5 分钟内会自动恢复（已在运行则不会重复启动）。

守护脚本用**托管虚拟环境**里的 python 直接启动 server.py，
不再需要 `uv run`，所以启动更快、离线也能用。
（如果虚拟环境缺失，会自动回退到原来的 `uv run` 方式。）

## 目录结构

| 文件 | 作用 |
| --- | --- |
| `settings.json` | 配置：端口、路径、宽限期、轮询间隔等 |
| `zotero-pdf2zh-autostart.ps1` | 守护进程（开机后常驻，负责拉起/守护 server） |
| `pdf2zh-autostart.ctl.ps1` | 控制台：安装 / 卸载 / 查看状态 / 启停 |
| `_common.ps1` | 公共函数（日志、健康检查、进程管理） |
| `logs\autostart.log` | 守护进程日志（**出问题时先看这个**） |
| `logs\server-<日期>.out.log` | server.py 的原始输出（翻译报错看这里） |

## 常用命令

在本目录 `E:\Zotero\PDF2Zh\autostart` 下打开 PowerShell，运行：

```powershell
# 查看状态（守护进程 / 翻译服务 / 计划任务）
powershell -NoProfile -ExecutionPolicy Bypass -File .\pdf2zh-autostart.ctl.ps1 status

# 查看最近日志
powershell -NoProfile -ExecutionPolicy Bypass -File .\pdf2zh-autostart.ctl.ps1 log

# 重启（改完 settings.json 后用这个）
powershell -NoProfile -ExecutionPolicy Bypass -File .\pdf2zh-autostart.ctl.ps1 restart

# 临时停止（不取消自启）
powershell -NoProfile -ExecutionPolicy Bypass -File .\pdf2zh-autostart.ctl.ps1 stop

# 取消开机自启（彻底恢复原状）
powershell -NoProfile -ExecutionPolicy Bypass -File .\pdf2zh-autostart.ctl.ps1 uninstall
```

## 配置说明（settings.json）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `port` | 8890 | 服务端口，需与 Zotero 插件里填的地址一致 |
| `serverDir` | `E:\Zotero\PDF2Zh\server` | server.py 所在目录 |
| `interpreter` | `auto` | `auto` / `venv` / `uv`，一般不用改 |
| `stopServerWhenZoteroExits` | `true` | Zotero 退出后是否自动停服务 |
| `zoteroExitGraceSeconds` | 120 | 退出后等多久才停（防止重启 Zotero 时反复启停） |
| `pollSeconds` | 5 | 检查 Zotero 的间隔 |
| `serverStartTimeoutSeconds` | 180 | 启动超时（首次创建环境可能较慢） |
| `checkUpdateOnStart` | `false` | 是否在启动时联网检查 Server 更新 |

> 改完 `settings.json` 后执行一次 `restart` 即可生效。

## 常见问题

**Q：怎么确认服务已经起来了？**
最直接：浏览器打开 <http://localhost:8890/health>，看到 `"status":"ok"` 就说明正常。
或者运行 `status` 命令，看到「翻译服务 : 正常」即可。

**Q：Zotero 里点翻译提示连不上？**
1. 运行 `status`，确认「翻译服务 : 正常」；
2. 检查插件设置里的 Server 地址是不是 `http://localhost:8890`；
3. 看 `logs\server-<日期>.err.log` 里的报错。

**Q：会不会重复启动好几个服务？**
不会。守护进程有单实例锁；启动前也会先探测 `/health`，已就绪就不重复拉起。

**Q：翻译到一半关掉 Zotero 会怎样？**
服务会在宽限期（默认 120 秒）后停止。如果正在翻译，建议等翻译完成再关 Zotero。
需要更长宽限期就调大 `zoteroExitGraceSeconds`。

**Q：想恢复成手动开终端？**
运行一次 `uninstall` 即可，脚本文件本身不会被删除。

**Q：首次翻译很慢？**
第一次翻译时 pdf2zh_next 需要下载模型/字体，属正常现象；
`serverStartTimeoutSeconds` 只约束**服务启动**，不约束翻译耗时。

## 卸载（完全恢复原状）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\pdf2zh-autostart.ctl.ps1 uninstall
Remove-Item -Recurse -Force E:\Zotero\PDF2Zh\autostart
```

第一条命令会删除计划任务和启动快捷方式，并停掉守护进程与翻译服务；
第二条删除本目录。之后就和安装前完全一样了。