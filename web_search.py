"""
DuckDuckGo web search wrapper.
Returns a formatted string with results, or None on failure/unavailable.
"""
from typing import Optional


def search(query: str, max_results: int = 5) -> Optional[str]:
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return None
        parts = []
        for r in results:
            title = r.get("title", "")
            body = r.get("body", "")
            href = r.get("href", "")
            parts.append(f"[{title}]\n{body}\nFonte: {href}")
        return "\n\n---\n\n".join(parts)
    except ImportError:
        return None
    except Exception:
        return None
