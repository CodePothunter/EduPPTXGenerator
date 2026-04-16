# Page Templates — SVG 参考模板

本目录存放各页面类型的参考 SVG 模板。LLM 生成 SVG 时会读取对应类型的模板作为视觉参考，
"照着画"而非"填充模板"。

## 文件命名

`{page_type}.svg` — 与 `PageType` 枚举值一一对应。

## 现有模板

| 文件 | 页面类型 | 状态 |
|------|---------|------|
| `cover.svg` | 封面页 | 占位 |
| `toc.svg` | 目录页 | 占位 |
| `section.svg` | 章节分隔页 | 占位 |
| `content.svg` | 正文内容页 | 占位 |
| `closing.svg` | 结束页 | 占位 |

## 制作指南

详见 `docs/page-template-guide.md`
