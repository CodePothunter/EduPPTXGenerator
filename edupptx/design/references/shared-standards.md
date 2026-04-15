# SVG 技术共享标准（PPT 兼容）

生成的 SVG 必须符合以下约束，确保在 PowerPoint 和浏览器中正常显示。
本文件由 Python 组装器加载，注入到 SVG 生成系统提示中。

---

## 1. 禁用特性黑名单

| 禁止使用 | 原因 | 替代方案 |
|---------|------|---------|
| `<foreignObject>` | PPT 不支持 | 用 `<text>` + `<tspan>` |
| CSS `@keyframes` / `animation` / `transition` | PPT 不支持动画 CSS | 静态设计 |
| `<style>` 中的复杂选择器 | PPT SVG 解析有限 | 内联 style 或属性 |
| JavaScript / `<script>` | 安全风险 | 不需要交互 |
| CSS `filter` | PPT 不支持 | SVG `<filter>` |
| `clip-path` 百分比 | PPT 解析失败 | 绝对坐标 |
| `rgba()` 颜色 | 兼容性差 | `fill-opacity` 属性分离 |
| group `opacity` | 渲染差异 | 逐元素设置 opacity |
| `<marker>` | PPT 不支持 | `<polygon>` 箭头 |
| 全页背景矩形 `width="1280" height="720"` | 背景由系统注入 | 不画全画布覆盖元素 |
| Emoji / Unicode 表情符号 | PPT 渲染不一致、无法配色、破坏设计统一性 | 使用 SVG 图标（`<g>` 内嵌图标路径）或纯文字符号（●、→、①②③） |

---

## 2. 画布规范

- `viewBox="0 0 1280 720"`（16:9 标准比例）
- 不设置 `width`/`height` 属性，让容器控制缩放
- **禁止绘制全页背景矩形**：不要画 `<rect width="1280" height="720" fill="..."/>` 或任何全画布覆盖元素
- 背景图由系统自动注入，只需画卡片和内容元素

---

## 3. 文字规范

- 只用 `<text>` + `<tspan>` 渲染文字，不使用 `<foreignObject>`
- 所有 `<text>` 元素必须使用完整 font-family：

  ```
  font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
  ```

- 用 `dy` 属性控制行间距，例如 `dy="1.4em"` 或 `dy="24"`
- 长文本手动分行，每个 `<tspan>` 一行，约 20-25 个中文字符换行
- 文字不能超出卡片边界

示例：

```svg
<text x="74" y="132" font-size="16"
      font-family="Noto Sans SC, 微软雅黑, Microsoft YaHei, Arial, Helvetica, sans-serif"
      fill="#333333">
  <tspan x="74" dy="0">第一行，不超过二十五个中文字符</tspan>
  <tspan x="74" dy="1.4em">第二行，继续内容</tspan>
</text>
```

---

## 4. 图片规范

- 用 `<image href="URL">` 嵌入图片
- 设置 `preserveAspectRatio="xMidYMid slice"` 防止变形
- 用 `<clipPath>` + `<rect rx="...">` 实现圆角图片

示例：

```svg
<defs>
  <clipPath id="imgClip">
    <rect x="60" y="140" width="200" height="150" rx="10"/>
  </clipPath>
</defs>
<image href="https://example.com/photo.jpg"
       x="60" y="140" width="200" height="150"
       preserveAspectRatio="xMidYMid slice"
       clip-path="url(#imgClip)"/>
```

---

## 5. 渐变和装饰规范

- 渐变定义在 `<defs>` 中
- 使用 `<linearGradient>` 或 `<radialGradient>`
- 装饰元素用低透明度（`opacity` 0.05–0.2），避免喧宾夺主

示例：

```svg
<defs>
  <linearGradient id="cardGrad" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="#4F46E5" stop-opacity="0.8"/>
    <stop offset="100%" stop-color="#7C3AED" stop-opacity="0.6"/>
  </linearGradient>
</defs>
<rect x="50" y="110" width="560" height="240" rx="14" fill="url(#cardGrad)"/>
```

---

## 6. 阴影规范

- 使用 SVG `<filter>` 实现阴影效果
- 定义在 `<defs>` 中，通过 `filter="url(#shadow)"` 引用
- 推荐参数：`dx="0" dy="2" stdDeviation="4" flood-opacity="0.1"`（轻阴影）

示例：

```svg
<defs>
  <filter id="shadow" x="-5%" y="-5%" width="110%" height="110%">
    <feDropShadow dx="0" dy="2" stdDeviation="4" flood-opacity="0.1"/>
  </filter>
</defs>
<rect x="50" y="110" width="560" height="240" rx="14"
      fill="#FFFFFF" filter="url(#shadow)"/>
```

---

## 7. Emoji 禁令

**绝对禁止在 SVG 中使用任何 Emoji / Unicode 表情符号**（如 🔍📋💡🎯✨🧩 等）。

原因：
- Emoji 在 PowerPoint 中渲染不一致（不同系统显示不同）
- Emoji 无法跟随主题配色，破坏视觉统一性
- Emoji 不够专业，降低教育课件的设计品质

**替代方案**：
- 需要视觉标记时，使用纯 SVG 图形：圆形编号（`<circle>` + 数字）、装饰色块（`<rect>` + 主色）、箭头（`<polygon>`）
- 需要列表前缀时，使用文字符号：`●`、`→`、`①②③④`、`▶`
- 如果系统提供了可用图标（在用户提示中列出），优先使用 SVG 图标嵌入

---

## 8. 输出格式

- SVG 代码用 ` ```svg ` 和 ` ``` ` 包裹
- 不附加任何解释文字
- 直接输出完整 SVG，不截断
