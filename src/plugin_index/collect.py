"""直接读取数据站现有目录，采集 PyPI 元信息并生成需要发布的完整结果。"""

import argparse
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit

from .changelog import fetch_markdown, release_notes
from .http import get_json
from .metadata import parse_project
from .protocol import build_catalog, package_key, validate_catalog, write_document

PYPI = "https://pypi.org"
_SIMPLE_JSON = "application/vnd.pypi.simple.v1+json"
log = logging.getLogger(__name__)


def load_previous(public_url: str, *, get=get_json) -> dict | None:
    """现场读取数据站目录作为唯一比较基准；首次 404 可初始化，其他读取失败阻止覆盖。"""
    if not public_url:
        return None
    parsed = urlsplit(public_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("public URL must be an HTTPS URL without credentials")
    try:
        return validate_catalog(get(public_url, timeout=10))
    except HTTPError as error:
        if error.code == 404:
            return None
        raise


def discover_packages() -> list[str]:
    """扫描 PyPI 总索引的名称启发候选；最终准入仍由插件声明决定，允许配置补录任意包名。"""
    payload = get_json(
        f"{PYPI}/simple/", limit=128 * 1024 * 1024, timeout=60, content_type=_SIMPLE_JSON
    )
    if not isinstance(payload, dict) or not isinstance(projects := payload.get("projects"), list):
        raise ValueError("PyPI Simple JSON index missing projects list")

    names: set[str] = set()
    for project in projects:
        name = project.get("name") if isinstance(project, dict) else None
        if not isinstance(name, str) or not (name := name.strip()):
            raise ValueError("PyPI Simple JSON index has invalid project name")
        if any(hint in package_key(name) for hint in ("automas", "auto-mas")):
            names.add(name)
    return sorted(names)


def collect(
    *,
    previous: dict | None,
    packages: list[str],
    discover: bool = False,
    get=get_json,
    discovery=discover_packages,
    fetch_notes=fetch_markdown,
    now: datetime | None = None,
) -> tuple[dict, dict]:
    """联合数据站已知包与补录包采集；已知读取失败不发布残缺目录，无变化保留原文档时间。"""
    if previous is not None:
        previous = validate_catalog(previous)
    if not isinstance(packages, list) or not all(
        isinstance(name, str) and name.strip() for name in packages
    ):
        raise ValueError("packages config must be a list of nonempty package names")
    configured = {package_key(name) for name in packages}
    prior_entries = {
        package_key(item["package_name"]): item for item in (previous or {}).get("plugins", [])
    }
    known = prior_entries.keys()
    names = configured | known
    if discover:
        names.update(package_key(name) for name in discovery())

    def read(name: str) -> tuple[str, dict | None, str | None]:
        """读取单个包的公开元数据；明确下架可移除，网络错误与失效声明保留为失败结果。"""
        try:
            payload = get(f"{PYPI}/pypi/{quote(name, safe='')}/json")
            if not isinstance(payload, dict) or not isinstance(payload.get("info"), dict):
                raise ValueError("PyPI response missing info")
            parsed = parse_project(payload)
            if parsed is None:
                if name in known | configured:
                    raise ValueError("known plugin declaration is invalid")
                return name, None, None
            draft, link = parsed
            if package_key(draft["package_name"]) != name:
                raise ValueError("PyPI package identity mismatch")
            prior = prior_entries.get(name)
            history = (
                prior["changelog"] if prior and prior["plugin_name"] == draft["plugin_name"] else []
            )
            draft["changelog"] = release_notes(link, payload, history, fetch=fetch_notes)
            return name, draft, None
        except HTTPError as error:
            if error.code == 404 and name not in configured:
                return name, None, None
            return name, None, f"HTTP {error.code}"
        except (OSError, ValueError, TypeError) as error:
            return name, None, type(error).__name__

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(read, sorted(names)))
    failures = [(name, error) for name, _, error in results if error]
    drafts = [draft for _, draft, _ in results if draft is not None]
    if failures and (not drafts or any(name in known | configured for name, _ in failures)):
        raise ValueError(f"catalog incomplete; keep published catalog: {failures}")
    for name, error in failures:
        log.warning("Skipping unknown candidate %s: %s", name, error)
    document = build_catalog(drafts, previous=previous, now=now)
    return document, {
        "changed": previous is None or document["revision"] != previous["revision"],
        "checked_packages": len(names),
        "plugins": len(drafts),
        "skipped": failures,
    }


def main() -> int:
    """读取实时公开目录并生成本轮临时文件及报告；仓库内不维护目录快照或采集状态。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-url", default=os.environ.get("AUTOMAS_PUBLIC_URL", ""))
    parser.add_argument("--packages", type=Path, default=Path("config/packages.json"))
    parser.add_argument("--output", type=Path, default=Path("api/v1/plugins.json"))
    parser.add_argument("--report", type=Path, default=Path("collect-report.json"))
    parser.add_argument("--discover", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        previous = load_previous(args.public_url)
        document, report = collect(
            previous=previous,
            packages=json.loads(args.packages.read_text(encoding="utf-8")),
            discover=args.discover or previous is None,
        )
        write_document(args.output, document)
        write_document(args.report, report)
    except (OSError, ValueError, TypeError) as error:
        write_document(args.report, {"error": str(error), "changed": False})
        log.error("Collection failed: %s", error)
        return 1
    log.info(
        "Catalog %s: %s plugins", "changed" if report["changed"] else "unchanged", report["plugins"]
    )
    return 0
