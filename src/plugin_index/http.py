"""有大小和时间限制的公开源读取，不读取或执行插件代码。"""

import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .protocol import MAX_BYTES


def get_bytes(url: str, *, limit: int = MAX_BYTES, timeout: int = 30) -> bytes:
    """读取公开 HTTPS 内容；只对临时 GET 失败重试，不重试真实 404。"""
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "AUTO-MAS-Plugin-Index/1.0"})
            with urlopen(request, timeout=timeout) as response:
                content = response.read(limit + 1)
            if len(content) > limit:
                raise ValueError("response exceeds size limit")
            return content
        except HTTPError as error:
            if error.code not in {408, 429, 500, 502, 503, 504} or attempt == 2:
                raise
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(0.5 * (attempt + 1))
    raise RuntimeError("GET attempts exhausted")


def get_json(url: str, *, timeout: int = 30) -> object:
    """读取受大小限制的 UTF-8 JSON，保留 HTTP 错误供调用者区分未发布和读取失败。"""
    return json.loads(get_bytes(url, timeout=timeout).decode("utf-8-sig"))
