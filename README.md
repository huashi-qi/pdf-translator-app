# PDF 翻译工具

把英文 PDF 拖进来，输出排版基本不变的**中文 PDF**。

下载即用 —— 内置 Python 运行时、字体、版面模型，**不需要装 Python、不需要 API Key**。

> **当前版本只上传源码。** 可执行版本（408 MB）请在 [Releases](../../releases) 下载。

---

## 功能

### 翻译
- **拖拽 PDF 即翻译**，输出保持原版排版（段落、表格、图片、公式位置都不乱）
- **免费引擎开箱可用**：Bing / Google，无需 Key
- 也支持 DeepSeek、硅基流动、通义、智谱、OpenAI、Gemini、Ollama（本地）等 16 种引擎
- **双语对照版** / **纯中文版** 可选
- 批量翻译、指定页码、跳过目录页
- 翻译中可见：进度条、剩余时间、批量队列、**实时步骤耗时**、**实时术语表命中**
- 可随时**终止翻译**

### PDF 编辑器（自由编辑）
不是「几个预设选项」，是真的能改：

| 能力 | 说明 |
| --- | --- |
| 文字 | 点选、拖动移位、**双击改内容**、删除 |
| 批量 | **框选多块**、Ctrl+A 全选、整体移动/改样式 |
| 样式 | 字号、颜色、粗体、斜体 |
| 绘制 | 白out、自由画笔、矩形、直线、插入图片 |
| 页面 | 缩略图导航、上移/下移、插入空白页、复制、旋转、裁剪、删除 |
| 编辑 | 撤销/重做（30 步）、Ctrl+滚轮缩放、**反色预览** |

### 其他工具
- **PDF 工具箱**：合并 / 拆分 / 旋转 / 压缩 / 页码 / 水印
- **术语表管理**：内置 **1100 条**电子工程术语，可增删改、导入导出、按领域筛选
- **从 PDF 学术语**：自动提取高频专业词（离线，不调 LLM）
- **质量检查**：漏翻、英文残留、数字错位、重复段落
- **快速查看**：只读浏览 + 加标注

### 界面
- 侧边栏布局（主推）/ 经典标签页布局，随时切换
- **白天 / 夜间 / 跟随系统**
- 界面缩放（0.8x ~ 1.6x）、字体大小（8~14）
- 窗口状态记忆、全局拖放、托盘图标

---

## 离线能力

**除了「翻译」本身，其他功能全部离线可用。**

| ✅ 离线可用 | ❌ 需联网 |
| --- | --- |
| PDF 编辑器全部功能 | 翻译（所有在线引擎） |
| PDF 工具箱 | |
| 术语表 / 术语学习 | |
| 质量检查、扫描检测 | |
| 历史记录、设置、主题 | |

内置离线资源（随安装包分发，不会运行时下载）：

| 资源 | 大小 |
| --- | --- |
| 排版字体（34 个） | 253.5 MB |
| 版面分析模型（ONNX） | 71.8 MB |
| Python 运行时 + 依赖 | 606 MB |

### 完全离线翻译

包内已内置 `ollama` 客户端。装 [Ollama](https://ollama.com) 后：

```
设置 → 翻译引擎 → 自定义填 ollama
API 地址 → http://127.0.0.1:11434
模型名   → qwen2.5:7b
```

---

## 从源码运行

```bash
# 需要 Python 3.12
pip install -r requirements.txt

# 复制配置
cp src/config.example.json config.json

# 启动（侧边栏版）
python src/app_sidebar.py

# 或经典版
python src/app.py
```

**打包成免安装版**（Windows）：

```powershell
.\src\build-app.ps1
```

---

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `app_sidebar.py` | 侧边栏版主程序（推荐） |
| `app.py` | 经典版主程序 + 全部后端 |
| `worker.py` | 翻译子进程，通过 `@@P@@` 事件与主进程通信 |
| `pdf_studio.py` | PDF 自由编辑器引擎 |
| `ui_studio.py` | PDF 编辑器界面 |
| `pdf_editor.py` | PDF 批量工具箱 |
| `pdf_viewer.py` | 快速查看 |
| `glossary_hook.py` | 术语注入（占位符方案，免费引擎也生效） |
| `glossary.csv` | 术语表 1100 条 |
| `learn_check.py` | 术语学习 + 译文质量检查 |
| `qa.py` | 术语自检 / 扫描检测 / 段落对照 |
| `ui_widgets.py` | 圆角按钮、Toast、卡片等自绘控件 |
| `sitecustomize.py` | 缓存重定向（把 babeldoc 缓存指到程序目录） |

---

## 技术要点

- **GUI**：Tkinter + ttk `clam` 主题（`vista`/`winnative` 会忽略配色）
- **高 DPI**：`SetProcessDpiAwarenessContext(-4)` + `tk scaling`
- **圆角按钮**：Tk 没有圆角矩形，用 `Canvas.create_polygon(smooth=True)` 自绘
- **术语替换**：babeldoc 的 glossary 只对 LLM 引擎生效，免费引擎改用**占位符**（`@@G1@@`）
  - 必须加**词边界**正则，否则 `PASS` → `PA接地`、`LENGTH` → `L使能GTH`
- **PDF 编辑**：PyMuPDF 没有「直接改已有文字」的 API，实现是**涂掉原文 + 重写**
  - 改样式前先比较差异，无变化直接跳过（否则原文会被无端重排）
- **缓存**：通过 `babeldoc.const.CACHE_FOLDER` 重定向，**不要改 HOME/USERPROFILE**（会破坏 Tk 文件对话框）

---

## 已知限制

- **翻译需要联网**（没有内置离线翻译模型）
- **目录页会被合并成一段**（babeldoc 的已知行为，已提供「跳过目录页」选项规避）
- **PDF 编辑器改文字后字距不可能 100% 还原**（因为机制是涂掉重写）
- 复杂的多栏/表格排版，编辑后可能错位

---

## 如何更新

改完代码后跑一条命令就推送：

```powershell
.\sync-and-push.ps1 -Message "说明" -Proxy
```

完整流程见 [UPDATING.md](UPDATING.md)。

---

## 版本历史

各版本的改动详见 [CHANGELOG.md](CHANGELOG.md)。

---

## 许可

**本项目以 AGPL-3.0 发布。**

它构建于以下 AGPL-3.0 项目之上：

| 项目 | 许可 |
| --- | --- |
| [babeldoc](https://github.com/funstory-ai/BabelDOC) | AGPL-3.0 |
| [pdf2zh_next](https://github.com/PDFMathTranslate/PDFMathTranslate-next) | AGPL-3.0 |

因为本程序**打包并导入了**上述 AGPL 代码，按 AGPL-3.0 的传染性条款，
本项目**必须以 AGPL-3.0 发布**（不能用更宽松的 GPL/MIT/Apache）。

> **分发要求**：如果你把本软件分发给他人，必须同时提供完整源码。
> 仅自己使用不涉及此要求。

完整条款见 [LICENSE](LICENSE)。

---

## 致谢

- [PDFMathTranslate / pdf2zh_next](https://github.com/PDFMathTranslate/PDFMathTranslate-next) — 翻译流程框架
- [BabelDOC](https://github.com/funstory-ai/BabelDOC) — PDF 版面解析与重建
- [PyMuPDF](https://pymupdf.readthedocs.io/) — PDF 读写引擎