"""
Best-effort web search and URL validation for the research pipeline and chat tools.

No API key required: uses the DuckDuckGo HTML endpoint. Any failure degrades
gracefully to an empty result set so the app keeps working offline.
"""
import html as html_mod
import logging
import re

import httpx

logger = logging.getLogger("chronokg.web_search")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 ChronoKG/0.1",
    "Accept-Language": "en-US,en;q=0.9",
}


def _strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return html_mod.unescape(re.sub(r"\s+", " ", text)).strip()


async def search_web(query: str, max_results: int = 5) -> list[dict]:
    """Search the web (DuckDuckGo HTML). Returns [{title, url, snippet}]."""
    query = query.strip()
    if not query:
        return []
    try:
        async with httpx.AsyncClient(timeout=6.0, follow_redirects=True, headers=_HEADERS) as client:
            resp = await client.get("https://html.duckduckgo.com/html/", params={"q": query})
            resp.raise_for_status()
            page = resp.text

        titles = re.findall(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page)
        snippets = re.findall(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', page)

        results = []
        for i, (raw_url, raw_title) in enumerate(titles[:max_results]):
            url = html_mod.unescape(raw_url)
            # DuckDuckGo wraps redirects; decode the uddg parameter when present
            m = re.search(r"uddg=([^&]+)", url)
            if m:
                try:
                    from urllib.parse import unquote
                    url = unquote(m.group(1))
                except Exception:
                    pass
            snippet = _strip_tags(snippets[i]) if i < len(snippets) else ""
            results.append({
                "title": _strip_tags(raw_title)[:300],
                "url": url[:1000],
                "snippet": snippet[:600],
            })
        return results
    except Exception as exc:
        logger.info("Web search unavailable (%s) — continuing without it.", type(exc).__name__)
        return []


async def validate_url(url: str) -> bool:
    """Best-effort reachability check for a study source URL."""
    if not url or not url.lower().startswith(("http://", "https://")):
        return False
    try:
        async with httpx.AsyncClient(timeout=4.0, follow_redirects=True, headers=_HEADERS) as client:
            resp = await client.head(url)
            if resp.status_code in (405, 501):  # HEAD unsupported — try GET
                resp = await client.get(url)
            return 200 <= resp.status_code < 400
    except Exception:
        return False
