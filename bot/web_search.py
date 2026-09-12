from __future__ import annotations

import html
import logging
import re
from typing import Any
import httpx

logger = logging.getLogger("bot.web_search")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,fa;q=0.8",
}


def _clean_html(raw_html: str) -> str:
    """حذف تگ‌های HTML و کاراکترهای اضافی."""
    text = re.sub(r"<[^<]+?>", "", raw_html)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


async def search_web(query: str, max_results: int = 3, timeout_sec: float = 4.0) -> list[dict[str, Any]]:
    """
    جستجوی سریع وب بدون نیاز به API Key یا ریسک تحریم با استفاده از موتور خزش سبک:
    نتایج شامل عنوان، خلاصه متن و منبع است.
    """
    if not query or not query.strip():
        return []

    clean_query = query.strip()
    url = "https://html.duckduckgo.com/html/"
    results: list[dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
            resp = await client.post(url, data={"q": clean_query}, headers=HEADERS)
            if resp.status_code != 200:
                logger.warning(f"Web search returned HTTP status {resp.status_code}")
                return []

            raw_text = resp.text
            blocks = re.findall(r'<div class="result__body">(.*?)</div>\s*</div>', raw_text, re.DOTALL)
            if not blocks:
                snippets = re.findall(r'class="result__snippet[^>]*>(.*?)</a>', raw_text, re.DOTALL)
                titles = re.findall(r'class="result__title[^>]*>(.*?)</a>', raw_text, re.DOTALL)
                for i in range(min(len(snippets), max_results)):
                    clean_s = _clean_html(snippets[i])
                    clean_t = _clean_html(titles[i]) if i < len(titles) else "مرجع فنی وب"
                    if clean_s:
                        results.append({
                            "title": clean_t,
                            "snippet": clean_s,
                            "source": "Web",
                        })
                return results

            for block in blocks[:max_results]:
                t_match = re.search(r'class="result__title[^>]*>(.*?)</a>', block, re.DOTALL)
                s_match = re.search(r'class="result__snippet[^>]*>(.*?)</a>', block, re.DOTALL)

                title = _clean_html(t_match.group(1)) if t_match else "مرجع فنی"
                snippet = _clean_html(s_match.group(1)) if s_match else ""

                if snippet:
                    results.append({
                        "title": title,
                        "snippet": snippet,
                        "source": "Web",
                    })

    except httpx.TimeoutException:
        logger.warning(f"Web search timed out for query: '{clean_query}'")
    except Exception as e:
        logger.error(f"Error during web search: {e}", exc_info=False)

    return results


def format_web_results_for_prompt(results: list[dict[str, Any]]) -> str:
    """فرمت‌بندی نتایج وب برای الصاق به پرامپت مهندسی."""
    if not results:
        return ""

    lines = ["--- نتایج استعلام زنده از وب و مستندات کارخانه ---"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. [{r['title']}]: {r['snippet']}")
    return "\n".join(lines)
