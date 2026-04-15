"""SVG 生成编排器 —— 并行调用 LLM 为每页生成 SVG。"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from loguru import logger

from edupptx.config import Config
from edupptx.design.prompts import build_svg_system_prompt, build_svg_user_prompt
from edupptx.llm_client import create_llm_client
from edupptx.models import GeneratedSlide, PlanningDraft, SlideAssets


def _extract_style_guide(template_path: Path) -> str:
    """从 SVG 模板中提取风格指南文本。

    读取模板 SVG 全文作为风格参考，LLM 会从中学习配色、字体、装饰风格。
    """
    if not template_path.exists():
        logger.warning("样式模板不存在: {}", template_path)
        return ""
    content = template_path.read_text(encoding="utf-8")
    # 截取合理长度，避免占用过多 token
    if len(content) > 8000:
        content = content[:8000] + "\n<!-- ... 截断 ... -->"
    return content


def _extract_svg(response: str) -> str:
    """从 LLM 响应中提取 SVG 内容。"""
    # 优先匹配 ```svg ... ``` 代码块
    m = re.search(r"```svg\s*\n(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    # 匹配 ```xml ... ```
    m = re.search(r"```xml\s*\n(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    # 匹配裸 <svg> 标签
    m = re.search(r"(<svg[\s\S]*?</svg>)", response)
    if m:
        return m.group(1).strip()
    # 回退：返回原文
    logger.warning("未能从 LLM 响应中提取 SVG，返回原始内容")
    return response.strip()


def _generate_one(
    client: LLMClient,
    system_prompt: str,
    page_plan: "PagePlan",
    assets: SlideAssets,
    total_pages: int,
    debug: bool = False,
) -> GeneratedSlide:
    """为单页生成 SVG（同步，运行在线程中）。"""
    from edupptx.models import PagePlan  # noqa: F811 — delayed for type hint

    user_prompt = build_svg_user_prompt(page_plan, assets, total_pages, debug=debug)
    logger.info("正在生成第 {}/{} 页 SVG ...", page_plan.page_number, total_pages)

    # Try up to 2 times (retry once on timeout)
    last_err = None
    for attempt in range(2):
        try:
            response = client.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
                max_tokens=16384,
            )
            break
        except Exception as e:
            last_err = e
            if attempt == 0:
                logger.warning("第 {} 页 SVG 生成失败，重试中: {}", page_plan.page_number, str(e)[:80])
    else:
        raise last_err

    svg_content = _extract_svg(response)
    logger.info("第 {} 页 SVG 生成完成，长度 {} 字符", page_plan.page_number, len(svg_content))

    return GeneratedSlide(
        page_number=page_plan.page_number,
        svg_content=svg_content,
    )


async def generate_slide_svgs(
    draft: PlanningDraft,
    all_assets: dict[int, SlideAssets],
    style_name: str,
    config: Config,
    debug: bool = False,
) -> list[GeneratedSlide]:
    """为所有页面并行生成 SVG。

    使用 ThreadPoolExecutor 并行调用同步 LLM 客户端。
    """
    # 1. 加载风格模板
    template_path = config.styles_dir / f"{style_name}.svg"
    style_guide = _extract_style_guide(template_path)
    if not style_guide:
        logger.warning("未找到风格模板 '{}'，将使用默认风格", style_name)

    # 2. 构建共享系统提示词（注入 VisualPlan 配色）
    system_prompt = build_svg_system_prompt(
        style_guide,
        visual_plan=draft.visual,
        content_density=draft.visual.content_density,
    )

    # 3. 初始化 LLM 客户端
    client = create_llm_client(config, web_search=False)
    total_pages = len(draft.pages)

    # 4. 并行生成
    results: list[GeneratedSlide] = []
    max_workers = min(total_pages, config.llm_concurrency)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_page = {}
        for page in draft.pages:
            page_assets = all_assets.get(
                page.page_number,
                SlideAssets(page_number=page.page_number),
            )
            future = executor.submit(
                _generate_one, client, system_prompt, page, page_assets, total_pages,
                debug=debug,
            )
            future_to_page[future] = page.page_number

        for future in as_completed(future_to_page):
            page_num = future_to_page[future]
            try:
                slide = future.result()
                results.append(slide)
            except Exception:
                logger.exception("第 {} 页 SVG 生成失败", page_num)
                # 生成失败时插入空白占位
                results.append(GeneratedSlide(
                    page_number=page_num,
                    svg_content=f'<svg viewBox="0 0 1280 720" xmlns="http://www.w3.org/2000/svg">'
                    f'<text x="640" y="360" text-anchor="middle" font-size="24" '
                    f'fill="#999">第 {page_num} 页生成失败</text></svg>',
                ))

    # 5. 按页码排序
    results.sort(key=lambda s: s.page_number)
    logger.info("SVG 生成完成，共 {} 页", len(results))
    return results
