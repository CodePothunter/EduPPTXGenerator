# 风格协商系统

EduPPTX 的一个独特能力：用户用自然语言描述风格偏好（如"简约商务风，配色偏冷"），LLM 将其转译为 StyleSchema JSON 补丁，自动调整整个演示文稿的视觉风格。

## 工作原理

```
用户输入: "适合初中生，风格清新"
                │
                ▼
        ┌───────────────┐
        │  LLM 风格协商  │  ← 提示词包含 StyleSchema 结构说明 + 风格映射参考
        │  (1 次调用)    │
        └───────┬───────┘
                │ JSON patch: {"layout": {"margin": "spacious", "content_density": "relaxed"}}
                ▼
        ┌───────────────┐
        │  深度合并到     │  ← base: styles/emerald.json
        │  基础 Schema   │
        └───────┬───────┘
                │ 完整的 StyleSchema
                ▼
        ┌───────────────┐
        │ style_resolver │  → ResolvedStyle（全部 EMU/hex 具体值）
        └───────────────┘
```

## 输入与输出

**输入**:
- `base_schema`: 从 `styles/*.json` 加载的基础 StyleSchema（由 Phase 1 的 palette 选择决定）
- `requirements`: 用户的自然语言要求字符串

**输出**: 修改后的 `StyleSchema`，保留了基础 schema 的所有未修改字段

**无风格要求时**: 如果用户只有内容要求（如"适合高中生"）而没有风格要求，LLM 返回空 patch `{}`，schema 保持原样。

## LLM 提示词设计

`style_negotiator.py` 中的提示词包含三部分：

### 1. Schema 结构说明

告诉 LLM 可以修改哪些字段：

```
global.palette.*     — 9 色调色板（primary, accent, accent_light, bg, text 等）
global.fonts.*       — 标题/正文字体
global.background.*  — 背景生成风格
semantic.*           — 字号、圆角、阴影参数
layout.*             — margin/card_spacing/icon_size/content_density 命名意图
decorations.*        — 装饰元素开关（下划线、面板、引用栏等）
```

### 2. 风格映射参考

预定义的风格关键词到 schema 修改的映射：

| 关键词 | 映射 |
|--------|------|
| 简约/极简/clean | 减少装饰，大边距(spacious)，低阴影 |
| 商务/专业/corporate | 冷色调(蓝灰系)，紧凑布局(comfortable/tight) |
| 活泼/年轻/playful | 暖色调(橙粉系)，大图标(large)，大圆角 |
| 学术/正式/academic | 深色文字，衬线字体，minimal 装饰 |
| 暗色/dark | 深色背景，浅色文字 |
| 紧凑 | tight margin + tight spacing + compact density |
| 宽松 | spacious margin + wide spacing + relaxed density |

这些映射是**参考**而非规则，LLM 可以组合或超出这些模式。

### 3. 输出格式

LLM 只输出需要修改的字段（增量 patch），不输出未变化的字段。减少输出 token，也降低误改风险。

## 深度合并

`_deep_merge(base, patch)` 递归合并补丁到基础 dict：

- 补丁中的叶子值覆盖基础值
- 补丁中的 dict 递归合并到基础 dict 的对应 key
- 未出现在补丁中的基础字段保持不变

示例：
```python
base = {"global": {"palette": {"accent": "#059669", "bg": "#F0FDF4"}}}
patch = {"global": {"palette": {"accent": "#2563EB"}}}
# 结果: {"global": {"palette": {"accent": "#2563EB", "bg": "#F0FDF4"}}}
```

合并后用 `StyleSchema.model_validate()` 验证。如果补丁导致验证失败（比如 LLM 写了无效的 margin 值），自动回退到基础 schema。

## 实际效果

以 "适合初中生，风格清新" 为例，LLM 返回的补丁：

```json
{
  "layout": {
    "margin": "spacious",
    "card_spacing": "wide",
    "content_density": "relaxed"
  },
  "semantic": {
    "card_shadow": {
      "blur_pt": 20,
      "dist_pt": 6,
      "alpha_pct": 12
    }
  }
}
```

这把边距从 80pt 放大到 120pt，卡片间距从 24pt 到 36pt，内容密度从 standard 到 relaxed（更大的内部间距）。阴影也变得更轻柔。整体视觉效果更"清新"。

## 与布局引擎的协同

风格协商的结果会影响布局计算。当 `content_density: "relaxed"` 导致卡片内部空间不足时，布局引擎的**自适应模式选择**会自动降级（从 full → compact → minimal），保证文字始终有足够空间。

这种协同是无感的：用户说"清新"，LLM 选择 relaxed 密度，布局引擎自动用更小的图标腾出空间。三层系统各做各的，最终结果自然正确。

详见 [layout-system.md](layout-system.md) 中的"自适应模式选择"部分。

## 会话产物

协商后的完整 schema 会保存在会话目录中：

```
output/session_xxx/style_schema.json
```

这个文件记录了 LLM 做出的所有风格决策，方便调试和复现。

## 容错设计

| 场景 | 处理 |
|------|------|
| LLM 调用失败 | 返回基础 schema，不中断 |
| LLM 返回无效 JSON | 捕获异常，返回基础 schema |
| 补丁导致 Pydantic 验证失败 | 回退到基础 schema |
| 用户无风格要求 | 跳过 LLM 调用，直接用基础 schema |
| LLM 返回空 patch `{}` | 识别为无修改，用基础 schema |
