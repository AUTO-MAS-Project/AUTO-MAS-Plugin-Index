"""有大小和时间限制的公开源读取，不读取或执行插件代码。"""

import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .protocol import MAX_BYTES


def get_bytes(
    url: str,
    *,
    limit: int = MAX_BYTES,
    timeout: int = 30,
    content_type: str | None = None,
) -> bytes:
    """读取公开 HTTPS 内容；指定类型时要求响应匹配，只对临时 GET 失败重试。"""
    headers = {"User-Agent": "AUTO-MAS-Plugin-Index/1.0"}
    if content_type is not None:
        headers["Accept"] = content_type
    for attempt in range(3):
        try:
            request = Request(url, headers=headers)
            with urlopen(request, timeout=timeout) as response:
                if (
                    content_type is not None
                    and (actual_type := response.headers.get_content_type()) != content_type
                ):
                    raise ValueError(
                        f"unexpected content type for {url}: {actual_type}; expected {content_type}"
                    )
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


def get_json(
    url: str,
    *,
    limit: int = MAX_BYTES,
    timeout: int = 30,
    content_type: str | None = None,
) -> object:
    """读取受大小限制的 UTF-8 JSON，保留 HTTP 错误供调用者区分未发布和读取失败。"""
    content = get_bytes(url, limit=limit, timeout=timeout, content_type=content_type)
    return json.loads(content.decode("utf-8-sig"))
