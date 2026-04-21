"""
Ollama HTTP client.

Uses httpx with a fresh client per request — avoids keep-alive pool state
issues that caused streams to block after the first response on Windows.
"""
import json
import threading
from typing import Callable, List, Dict, Optional

import httpx

BASE_URL = "http://localhost:11434"
_CONNECT_TIMEOUT = 10.0   # seconds to establish connection
# read=None → no timeout waiting for tokens; Qwen3 thinks silently for minutes


def check_health() -> bool:
    try:
        r = httpx.get(f"{BASE_URL}/", timeout=5.0)
        return r.status_code == 200
    except Exception:
        return False


def list_models() -> List[str]:
    try:
        r = httpx.get(f"{BASE_URL}/api/tags", timeout=10.0)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


def stream_chat(
    model: str,
    messages: List[Dict],
    on_token: Callable[[str], None],
    on_thinking: Optional[Callable[[str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> None:
    """
    Stream a chat completion.

    Calls on_token(token) for each content token.
    Calls on_thinking(text) for thinking tokens (Qwen3 reasoning mode).
    Stops cleanly when cancel_event is set.
    Raises httpx exceptions on connection failure (caught in caller).
    """
    payload = {"model": model, "messages": messages, "stream": True}
    client = httpx.Client(
        timeout=httpx.Timeout(
            connect=_CONNECT_TIMEOUT,
            read=None,   # wait indefinitely for chunks — thinking can take minutes
            write=None,
            pool=None,
        )
    )
    try:
        with client.stream("POST", f"{BASE_URL}/api/chat", json=payload) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if cancel_event and cancel_event.is_set():
                    response.close()
                    return
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = chunk.get("message") or {}
                thinking = msg.get("thinking") or ""
                if thinking and on_thinking:
                    on_thinking(thinking)
                token = msg.get("content") or ""
                if token:
                    on_token(token)
                if chunk.get("done"):
                    response.close()   # don't drain keep-alive buffer
                    return
    except (httpx.StreamClosed, ConnectionAbortedError, OSError):
        # Socket closed by cancel or OS — not an error
        return
    finally:
        try:
            client.close()
        except Exception:
            pass
