---
name: i18
description: Use when translating business i18n JSON payloads that use importList, languageCode, translationImportList, textId, and localeName, or when the user wants localized text written into this project's Excel template. Trigger for locale-pack translation, project language code mapping, Thai/Japanese/etc. UI copy, or when an Excel workbook needs upsert-by-appId into the Sheet1 template with appId/简体中文/简体中文/目标语言 columns.
---

# i18 项目语言包与 Excel 落表

## 任务类型

- 翻译 `importList` 结构的 i18n JSON。
- 按项目语言 code 输出目标语言，例如泰语=`th`、英语=`en`、日语=`ja`。
- 将翻译结果写入项目语言 Excel。

## JSON 翻译规则

- 保持 JSON 结构不变，只翻译 `localeName` 这类展示文案。
- 保持 `textId`、键名、层级原样不变。
- 保持占位符原样不变，包括 `{{name}}`、`{0}`、`%s`、`%(name)s`、`$1`、`{name}`。
- 未指定目标语言时先确认目标语言。
- 默认使用专业、清晰、UI 友好的译法。
- 输出的 `languageCode` 优先使用项目配置中的 `code` 原值，不扩展成其他 locale 变体。

## 项目语言 code 映射

- 简体中文：`zh_CN`
- 繁体中文：`zh_TW`
- 英语：`en`
- 日语：`ja`
- 俄语：`ru`
- 意大利语：`it`
- 法语：`fr`
- 德语：`de`
- 西班牙语：`es`
- 韩语：`ko`
- 波兰语：`Polish`
- 捷克语：`Czech`
- 泰语：`th`
- 葡萄牙语：`pt`
- 荷兰语：`af`
- 印尼语：`ind`
- 阿拉伯语：`ar`
- 土耳其语：`tr`
- 马来语：`ms`
- 印地语：`hi`
- 菲律宾语：`fil`
- 越南语：`vi`
- 乌克兰语：`uk`
- 保加利亚语：`bg`
- 希腊语：`el`
- 高棉语：`km`
- 巴西葡萄牙语：`pt-BR`，当前为 `DISABLED`

## Excel 模板流程

用户同时提供语言 Excel 时，继续执行 Excel 落表。

默认模板：

- 工作表：`Sheet1`
- 表头：`appId` / `简体中文` / `简体中文` / `目标语言`
- 匹配键：`appId`

默认行为：

- 只处理已存在目标语言列的工作簿，不自动新增语言列。
- 默认生成新副本，不覆盖源文件。
- 默认命名：`原文件名-新增{语言}.xlsx`
- `B`、`C` 两列始终写同一份中文。
- 若 `appId` 已存在，更新现有行，不新增重复行。
- 若 `appId` 不存在，追加到末尾，并复制末尾数据行样式。
- 保持其他工作表不变。

遇到以下情况先停下确认：

- `Sheet1` 不存在
- 表头不是 `appId / 简体中文 / 简体中文 / 目标语言`
- 目标语言列不存在
- 用户要求按排序插入或按业务分组插入

## Excel 脚本

优先使用项目内脚本：

```bash
python scripts/upsert_language_workbook.py --source <xlsx> --output <xlsx> --sheet Sheet1 --lang-header <语言名> --entries-file <json>
```

`entries-file` 必须是有序 JSON 数组：

```json
[
  { "appId": "orcaExample", "zh": "中文文案", "target": "目标语言文案" }
]
```

脚本行为：

- 校验工作表和表头
- 按 `appId` 执行 upsert
- 追加新行时复制末尾数据行样式
- 输出 JSON 摘要，包含新增数、更新数、总行数

## 输出前自检

- `textId` / `appId` 无漏项
- 占位符逐字符一致
- `languageCode` 与项目 code 一致
- Excel 新文件可打开
- `Sheet2`、其他未涉及工作表保持不变
- 重复 `appId` 未产生重复行
