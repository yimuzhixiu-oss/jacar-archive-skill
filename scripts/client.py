"""JACAR HTTP 会话：UA、限速、指数退避、Range 续传。

设计要点：
- 全站统一走一个 Session，复用连接。
- 默认 3 秒间隔；robots.txt 声明的 Crawl-delay 是 30，--delay 30 可完全守规。
- 429 / 403 / 503 视为限流，指数退避；连续失败抛 RateLimited 让上层整批停。
"""

from __future__ import annotations

import time

import requests

BASE = "https://www.jacar.archives.go.jp"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


class RateLimited(RuntimeError):
    """站点限流，整批应立即停止而不是继续重试。"""


class JacarClient:
    def __init__(self, delay: float = 3.0, retries: int = 3, timeout: int = 60):
        self.delay = max(0.0, delay)
        self.retries = max(0, retries)
        self.timeout = timeout
        self._last = 0.0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ja,en;q=0.9,zh;q=0.8",
                "Connection": "keep-alive",
            }
        )

    # ── 限速 ────────────────────────────────────────────
    def _throttle(self) -> None:
        if self.delay <= 0:
            return
        gap = time.monotonic() - self._last
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last = time.monotonic()

    # ── 核心请求 ────────────────────────────────────────
    def request(self, method: str, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                resp = self.session.request(method, url, **kwargs)
            except requests.RequestException as exc:
                last_exc = exc
                if attempt >= self.retries:
                    raise
                time.sleep(2 ** attempt)
                continue

            if resp.status_code in (429, 503):
                if attempt >= self.retries:
                    raise RateLimited(
                        f"{resp.status_code} 连续 {self.retries + 1} 次，站点疑似限流，建议隔一段时间再跑"
                    )
                wait = 30 * (2 ** attempt)
                print(f"  ! {resp.status_code} 限流，等待 {wait}s 后重试（第 {attempt + 1} 次）")
                time.sleep(wait)
                continue

            if resp.status_code in (403,):
                raise RateLimited(f"403 拒绝访问：{url}")

            return resp
        raise last_exc  # pragma: no cover

    def get(self, url: str, **kwargs) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, data=None, **kwargs) -> requests.Response:
        return self.request("POST", url, data=data, **kwargs)

    # ── 便捷方法 ────────────────────────────────────────
    def text(self, url: str, **kwargs) -> str:
        resp = self.get(url, **kwargs)
        resp.raise_for_status()
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text

    def download(self, url: str, dest, *, resume: bool = True, chunk: int = 65536) -> int:
        """下载到 dest（Path）。支持 Range 续传，返回最终字节数。"""
        from pathlib import Path

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        existing = dest.stat().st_size if (resume and dest.exists()) else 0
        headers = {"Range": f"bytes={existing}-"} if existing else {}

        resp = self.get(url, headers=headers, stream=True)
        if existing and resp.status_code == 200:
            existing = 0  # 服务器不支持 Range，从头来
        resp.raise_for_status()

        mode = "ab" if existing else "wb"
        total = existing
        with open(dest, mode) as fh:
            for block in resp.iter_content(chunk):
                if block:
                    fh.write(block)
                    total += len(block)
        return total


def make_client(delay: float = 3.0, retries: int = 3, timeout: int = 60) -> JacarClient:
    return JacarClient(delay=delay, retries=retries, timeout=timeout)
