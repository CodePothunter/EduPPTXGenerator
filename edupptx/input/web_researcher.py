"""Web research via Tavily API."""

from __future__ import annotations

import httpx


async def research_topic(topic: str, api_key: str, max_results: int = 5) -> str:
    """Search the web for topic information, return a summary string."""
    if not api_key:
        return ""

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": topic,
                    "max_results": max_results,
                    "include_answer": True,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, Exception):
        return ""

    parts: list[str] = []

    answer = data.get("answer")
    if answer:
        parts.append(f"Summary: {answer}")
        parts.append("")

    for result in data.get("results", []):
        title = result.get("title", "")
        content = result.get("content", "")
        if title or content:
            parts.append(f"- {title}: {content}")

    return "\n".join(parts)
