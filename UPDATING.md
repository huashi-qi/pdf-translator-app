## 如何更新

### 一句话

改完 `app\` 里的代码，跑一条命令就推送了：

```powershell
.\sync-and-push.ps1 -Message "修复 xxx" -Proxy
```

---

### 目录约定

| 目录 | 作用 | 是否入库 |
| --- | --- | --- |
| **`app\`** | **你的开发目录**（有 config.json、cache、vendor） | ❌ 不入库 |
| **`src\`** | **仓库的源码目录**（只有源代码） | ✅ 入库 |

> 改代码时直接改 `app\`，脚本会自动同步到 `src\`。

---

### 完整流程

```
改代码 (app\)
    ↓
.\sync-and-push.ps1
    ├─ 1. app\ → src\  自动同步（只同步有变化的）
    ├─ 2. 体积检查     防止误传大文件
    ├─ 3. git commit   自动或自定义提交信息
    └─ 4. git push     推送（带 -Proxy 走代理）
```

---

### 三种用法

**① 最常用**

```powershell
.\sync-and-push.ps1 -Message "修复编辑器滚动" -Proxy
```

**② 先看会改什么（不实际推送）**

```powershell
.\sync-and-push.ps1 -DryRun
```

**③ 网络正常时（不加 -Proxy 更快）**

```powershell
.\sync-and-push.ps1 -Message "新增快捷键"
```

---

### ⚠️ 这台机器的特殊情况

`github.com:443` **直连不通**（`api.github.com` 能通），所以推送必须走代理：

```
github.com                    443 不通   ← 推送走这个域名
api.github.com                443 可达
objects.githubusercontent.com 443 可达
```

脚本的 `-Proxy` 参数会自动：

1. 推送前 `git config http.proxy http://127.0.0.1:7897`
2. 推送完**自动清理**（不影响你其它 git 操作）

> 如果你的代理端口不是 7897，用 `-ProxyUrl http://127.0.0.1:端口` 指定。

---

### 手动推送（不想用脚本）

```powershell
cd E:\Zotero\PDF2Zh

# 1. 同步源码
Copy-Item app\*.py app\*.md app\*.csv src\ -Force

# 2. 提交
git add -A
git commit -m "你的说明"

# 3. 推送（走代理）
git config http.proxy http://127.0.0.1:7897
git push
git config --unset http.proxy
```

---

### 发新版本（Release）

源码推送完成后，如果要发布**新的安装包**：

**① 打包**

```powershell
cd E:\Zotero\PDF2Zh
.\app\build-app.ps1
```

产物：`release\PDF翻译助手.zip`（约 408 MB）

**② 在 GitHub 上发 Release**

1. 打开 https://github.com/huashi-qi/pdf-translator-app/releases
2. 点 **Draft a new release**
3. **Choose a tag** → 输入新版本号（如 `v1.1.0`）→ **Create new tag**
4. Title 填 `PDF翻译工具 v1.1.0`
5. Description 写这版改了什么（可直接从 CHANGELOG 复制）
6. 把 `release\PDF翻译助手.zip` **拖进附件区**
7. 点 **Publish release**

> 附件上传 408 MB 大约 5 分钟（我实测 301 秒）。

**③ 更新 CHANGELOG**

在 `CHANGELOG.md` 里加新版本段落：

```markdown
## [1.1.0] - 2026-XX-XX

### 新增
- xxx

### 已修复
- xxx
```

然后把 `[未发布]` 的对比链接改成 `v1.0.0...v1.1.0`。

---

### 常见问题

**Q：推送报 `Failed to connect to github.com port 443`**

A：加 `-Proxy` 参数。这是本机网络问题，不是仓库问题。

**Q：推送报 `ghp_` 相关或 403**

A：Token 过期了。重新生成一个（勾 `repo` 权限），然后：

```powershell
git remote set-url origin https://新token@github.com/huashi-qi/pdf-translator-app.git
git push
git remote set-url origin https://github.com/huashi-qi/pdf-translator-app.git  # 推完清掉
```

**Q：不小心提交了大文件**

A：先把文件加进 `.gitignore`，然后：

```powershell
git rm --cached 大文件名
git commit -m "移除大文件"
```

如果已经 push 了，需要改写历史（`git filter-repo`），或直接在 GitHub 上删仓库重来。

**Q：想回退到上一个版本**

```powershell
git log --oneline          # 看提交历史
git revert <commit_id>     # 撤销某次提交（安全）
git push
```

---