---
name: wechat-article-to-markdown
description: Use when the user provides a public WeChat Official Account article URL from mp.weixin.qq.com and wants it converted, saved, archived, summarized from, or ingested as local Markdown. This project-local skill converts WeChat articles to Markdown, extracts metadata, downloads article images, and rewrites image links to local files.
---

# 微信公众号文章转 Markdown

## 目标

把公开的微信公众号文章链接转换为本地 Markdown 文件，用于归档、总结或知识库整理。

默认输出：

- Markdown：`.tmp/wechat-article-to-markdown/output/<文章标题>/<文章标题>.md`
- 图片：`.tmp/wechat-article-to-markdown/output/<文章标题>/images/*`

## 工作流

1. 只接受 `https://mp.weixin.qq.com/s/...` 或可规范化为该格式的公开文章链接。
2. 优先使用本 skill 的自包含脚本，它会直接请求文章 HTML，解析 `#js_content`，提取标题、公众号、发布时间，并下载图片。
3. 必须保留微信文章中的源码块：遇到 `pre` / `code-snippet__*` 结构时输出 fenced code block，不要把源码块解析成正文标题、列表或表格。
4. 必须保留普通 Markdown 表格：表格行之间使用单个换行连续输出，不要在表头、分隔行、数据行之间插入空行；源码块里的表格文本仍保持在 fenced code block 内。
5. 转换后必须做轻量验证：Markdown 文件存在、前几行元数据可读、图片数量合理、Markdown 中没有残留的微信远程图片链接，并抽查源码示例附近是否保留 ``` fenced block、普通表格是否是连续 `| ... |` 行。
6. 回复用户时给出标题、公众号、发布时间、Markdown 绝对路径、图片下载数量，以及任何失败点。

## 钉钉知识库写入

当用户要求把微信公众号文章写入钉钉文档或知识库时：

1. 默认写入知识库 `袁帅林的知识库`（workspaceId：`dN0G71dwkkBoXWYK`）下的 `微信文章` 文件夹，不要写到知识库根目录。
2. 先用钉钉文档 MCP `list_nodes(workspaceId=...)` 查找根目录下是否已有 `微信文章` 文件夹。
3. 如果文件夹不存在，使用 `create_file(type="folder", workspaceId=..., name="微信文章")` 创建；如果已存在，复用其 `nodeId` 作为 `folderId`。
4. 创建文章文档时使用 `create_document(folderId=<微信文章文件夹 nodeId>, name=<文章标题>, markdown=<转换后的 Markdown>)`。
5. 钉钉写入版 Markdown 优先保留微信远程图片 URL，让钉钉导入为文档资源；本地归档版再下载图片到 `images/`。
6. 写入后必须用 `get_document_content(nodeId=...)` 读回，确认标题、源码块、普通表格和图片导入结果。
7. 如果文章已经错误写到根目录，且当前 MCP 没有移动节点接口，先在 `微信文章` 文件夹下重建文档并验证；不要未确认就删除根目录旧文档。

## 推荐命令

在 `E:\mySkills` 仓库根目录执行：

```powershell
$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; python .\.codex\skills\wechat-article-to-markdown\scripts\convert_wechat_article.py "https://mp.weixin.qq.com/s/..."
```

说明：

- 脚本源码完全位于本 skill 的 `scripts/` 目录；`.tmp/wechat-article-to-markdown` 只作为输出和 debug 目录，不作为源码或虚拟环境依赖。
- 脚本依赖当前 Python 环境中的 `httpx` 和 `beautifulsoup4`；如果缺失，先说明缺失依赖，不要把依赖安装到全局 Python，除非用户明确授权。
- 设置 `PYTHONUTF8` 和 `PYTHONIOENCODING`，避免 Windows PowerShell GBK 控制台无法打印中文或图标。
- 当前已验证：直接请求 HTML 的路径比浏览器自动化入口更稳定；如果标题或正文为空，可能遇到验证码、权限限制或文章不可公开访问。

## 验证命令

```powershell
$md = Get-ChildItem -Recurse .\.tmp\wechat-article-to-markdown\output -Filter *.md | Sort-Object LastWriteTime -Descending | Select-Object -First 1
$md.FullName
Get-Content -Encoding UTF8 $md.FullName -TotalCount 30
Get-ChildItem (Join-Path $md.DirectoryName 'images') | Measure-Object | Select-Object -ExpandProperty Count
rg -n "mmbiz\.qpic|mmbiz\.qlogo|https://mmbiz|http://mmbiz" $md.FullName
```

`rg` 没有输出通常表示微信远程图片链接已替换为本地相对路径。

## 失败处理

- 如果提示 `httpx` 或 `bs4` 依赖缺失，先报告缺失依赖；只有用户明确授权后再创建隔离虚拟环境或安装依赖。
- 如果没有标题或正文，保存抓取到的 HTML 作为 debug 文件，并说明可能遇到验证码、权限限制或文章不可公开访问。
- 如果部分图片下载失败，仍保留 Markdown，并在回复中列出成功/失败数量。
- 如果用户只是要写入钉钉/知识库，优先使用生成的 Markdown 内容；图片下载失败不阻塞正文入库。
