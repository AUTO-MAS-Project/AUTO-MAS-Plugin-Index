"""从插件作者公布的 Markdown 文件提取 PyPI 最近五个版本的说明。"""

import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit

import urllib3
from markdown_it import MarkdownIt
from packaging.version import InvalidVersion, Version

log = logging.getLogger(__name__)
MAX_MARKDOWN_BYTES = 512 * 1024


def fetch_markdown(url: str) -> str:
    """从公开 HTTPS 地址读取文档，每次重定向都重新检查并固定实际连接的地址。"""
    for _ in range(4):
        parsed = urlsplit(url)
        host = parsed.hostname
        if parsed.scheme != "https" or not host or parsed.username or parsed.password:
            raise ValueError("changelog must be public HTTPS")
        path = parsed.path
        if host == "github.com":
            parts = path.split("/", 4)
            if len(parts) == 5 and parts[3] == "blob":
                host = "raw.githubusercontent.com"
                path = f"/{parts[1]}/{parts[2]}/{parts[4]}"
        port = parsed.port or 443
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        address = ipaddress.ip_address(addresses[0][4][0])
        if not address.is_global or address.is_multicast:
            raise ValueError("changelog must resolve to a public address")
        with urllib3.HTTPSConnectionPool(
            str(address),
            port,
            server_hostname=host,
            assert_hostname=host,
            timeout=urllib3.Timeout(connect=4, read=8),
        ) as pool:
            response = pool.urlopen(
                "GET",
                urlunsplit(("", "", path or "/", parsed.query, "")),
                headers={
                    "Host": host if port == 443 else f"{host}:{port}",
                    "Accept-Encoding": "identity",
                },
                preload_content=False,
                redirect=False,
                retries=False,
            )
            try:
                redirect = response.get_redirect_location()
                if redirect:
                    url = urljoin(url, redirect)
                    continue
                if response.status != 200:
                    raise ValueError(f"changelog returned HTTP {response.status}")
                data = response.read(MAX_MARKDOWN_BYTES + 1, decode_content=False)
                if len(data) > MAX_MARKDOWN_BYTES:
                    raise ValueError("changelog exceeds size limit")
                return data.decode("utf-8-sig")
            finally:
                response.close()
    raise ValueError("too many changelog redirects")


def parse_sections(markdown: str, window: dict[Version, str]) -> list[dict]:
    """按 Markdown 的真实二级标题分段，保留正文；代码块里的伪标题不分段。"""
    lines = markdown.splitlines()
    tokens = MarkdownIt().parse(markdown)
    sections = {}
    end = len(lines)
    for i in reversed(range(len(tokens))):
        token = tokens[i]
        if token.type != "heading_open" or token.level or token.tag not in {"h1", "h2"}:
            continue
        start, body = token.map
        if token.tag == "h2":
            title = tokens[i + 1].content.split()
            try:
                version = Version(title[0].strip("[]")) if title else None
            except InvalidVersion:
                version = None
            if version in window and (content := "\n".join(lines[body:end]).strip()):
                sections[version] = content
        end = start
    return [{"version": text, "body": sections[v]} for v, text in window.items() if v in sections]


def release_notes(
    link: str, project: dict, previous: list[dict], *, fetch=fetch_markdown
) -> list[dict]:
    """用同一份 PyPI 响应确定版本窗口；读取失败只复用同插件且仍在窗口内的记录。"""
    if not link:
        return []
    target = Version(project["info"]["version"])
    published = {}
    for text, files in project.get("releases", {}).items():
        if not files:
            continue
        try:
            version = Version(text)
        except InvalidVersion:
            continue
        if version <= target:
            published[version] = text
    window = {v: published[v] for v in sorted(published, reverse=True)[:5]}
    try:
        return parse_sections(fetch(link), window)
    except (OSError, ValueError, urllib3.exceptions.HTTPError) as error:
        log.warning(
            "Changelog unavailable for %s: %s", project["info"]["name"], type(error).__name__
        )
        return [note for note in previous if Version(note["version"]) in window]
