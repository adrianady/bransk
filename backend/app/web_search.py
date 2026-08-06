"""
Best-effort web search and URL validation for the research pipeline and chat tools.

No API key required: uses the DuckDuckGo HTML endpoint. Any failure degrades
gracefully to an empty result set so the app keeps working offline.
"""
import html as html_mod
import logging
import os
import re

import anyio
import httpx

from app.studies_store import assert_public_download_url

logger = logging.getLogger("chronokg.web_search")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 ChronoKG/0.1",
    "Accept-Language": "en-US,en;q=0.9",
}

SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")


def _strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return html_mod.unescape(re.sub(r"\s+", " ", text)).strip()


async def search_web(query: str, max_results: int = 5) -> list[dict]:
    """Search the web. Returns [{title, url, snippet}].

    Provider order: Serper (if key) → Brave (if key) → DuckDuckGo HTML.
    Any failure degrades to the next provider, then to an empty result set.
    """
    query = query.strip()
    if not query:
        return []
    if SERPER_API_KEY:
        results = await _search_serper(query, max_results)
        if results is not None:
            return results
    if BRAVE_API_KEY:
        results = await _search_brave(query, max_results)
        if results is not None:
            return results
    return await _search_duckduckgo(query, max_results)


async def _search_serper(query: str, max_results: int) -> list[dict] | None:
    """Google results via Serper.dev (API key). Returns None on failure."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
                json={"q": query, "num": max_results},
            )
            resp.raise_for_status()
            organic = resp.json().get("organic") or []
        return [
            {
                "title": (r.get("title") or "")[:300],
                "url": (r.get("link") or "")[:1000],
                "snippet": (r.get("snippet") or "")[:600],
            }
            for r in organic[:max_results]
        ]
    except Exception as exc:
        logger.info("Serper search failed (%s), trying next provider.", type(exc).__name__)
        return None


async def _search_brave(query: str, max_results: int) -> list[dict] | None:
    """Brave search API. Returns None on failure."""
    try:
        async with httpx.AsyncClient(timeout=8.0, headers={"X-Subscription-Token": BRAVE_API_KEY}) as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": max_results},
            )
            resp.raise_for_status()
            results = (resp.json().get("web") or {}).get("results") or []
        return [
            {
                "title": (r.get("title") or "")[:300],
                "url": (r.get("url") or "")[:1000],
                "snippet": (r.get("description") or "")[:600],
            }
            for r in results[:max_results]
        ]
    except Exception as exc:
        logger.info("Brave search failed (%s), trying next provider.", type(exc).__name__)
        return None


async def _search_duckduckgo(query: str, max_results: int) -> list[dict]:
    """DuckDuckGo HTML (no API key). Returns [] when offline."""
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
        # Same SSRF guard as study PDF downloads: refuse private/loopback/
        # link-local hosts and non-standard ports before touching the network.
        await anyio.to_thread.run_sync(assert_public_download_url, url)
    except ValueError:
        return False
    try:
        async def _guard_hop(response: httpx.Response) -> None:
            # A redirect can silently point at an internal host — re-validate
            # every hop the same way the download path does.
            try:
                await anyio.to_thread.run_sync(assert_public_download_url, str(response.url))
            except ValueError as exc:
                raise httpx.HTTPError(f"Blocked redirect to non-public URL: {exc}") from exc

        async with httpx.AsyncClient(
            timeout=4.0,
            follow_redirects=True,
            headers=_HEADERS,
            event_hooks={"response": [_guard_hop]},
            max_redirects=8,
        ) as client:
            resp = await client.head(url)
            if resp.status_code in (405, 501):  # HEAD unsupported — try GET
                resp = await client.get(url)
            return 200 <= resp.status_code < 400
    except Exception:
        return False
