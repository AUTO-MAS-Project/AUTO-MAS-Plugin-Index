"""现场读取已发布目录，守护完整采集、无变化和元信息修正的可见契约。"""

import json
from copy import deepcopy
from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.response import addinfourl

import jsonschema
import pytest

from plugin_index import http
from plugin_index.collect import collect, read_public_catalog
from plugin_index.protocol import build_catalog, validate_catalog


def _index_response(url, content_type, body):
    headers = Message()
    headers["Content-Type"] = content_type
    return addinfourl(BytesIO(body), headers, url, 200)


def test_discovery_requests_json_and_keeps_known_packages(catalog, project, monkeypatch):
    """从 JSON 发现新包；总索引暂时缺少已收录包时，仍从单包接口刷新其版本。"""
    media_type = "application/vnd.pypi.simple.v1+json"

    def read_index(request, *, timeout):
        assert request.full_url == "https://pypi.org/simple/"
        assert request.get_header("Accept") == media_type
        body = json.dumps(
            {
                "meta": {"api-version": "1.4"},
                "projects": [{"name": "Auto_MAS_Fresh"}, {"name": "unrelated-package"}],
            }
        ).encode()
        return _index_response(request.full_url, media_type, body)

    new_project = deepcopy(project)
    new_project["info"]["name"] = "auto-mas-fresh"
    new_project["info"]["keywords"] = "auto-mas-plugin,auto-mas-id:fresh"
    project["info"]["version"] = "9.1.0"
    responses = {
        "https://pypi.org/pypi/auto-mas-demo/json": project,
        "https://pypi.org/pypi/auto-mas-fresh/json": new_project,
    }
    monkeypatch.setattr(http, "urlopen", read_index)

    document, _ = collect(previous=catalog, packages=[], discover=True, get=responses.__getitem__)

    assert {item["package_name"]: item["version"] for item in document["plugins"]} == {
        "auto-mas-demo": "9.1.0",
        "auto-mas-fresh": catalog["plugins"][0]["version"],
    }


@pytest.mark.parametrize(
    ("content_type", "body", "error"),
    [
        ("text/html", b"<a href='/simple/auto-mas-demo/'>auto-mas-demo</a>", "content type"),
        ("application/vnd.pypi.simple.v1+json", b'{"projects":{}}', "projects list"),
    ],
)
def test_invalid_index_aborts_discovery(catalog, project, monkeypatch, content_type, body, error):
    """响应格式错误时终止采集，避免把错误当成没有新包。"""
    monkeypatch.setattr(
        http,
        "urlopen",
        lambda request, **_: _index_response(request.full_url, content_type, body),
    )
    with pytest.raises(ValueError, match=error):
        collect(previous=catalog, packages=[], discover=True, get=lambda _: project)


def test_wire_contract_and_metadata_only_change(catalog, project):
    """无变化复用原时间；版本不变但文档地址变化时也生成新目录摘要。"""
    schema = json.loads((Path(__file__).parents[1] / "schema/plugin-index.schema.json").read_text())
    jsonschema.validate(catalog, schema, format_checker=jsonschema.FormatChecker())
    document, report = collect(previous=catalog, packages=[], get=lambda _: project)
    assert document == catalog
    assert not report["changed"]
    project["info"]["project_urls"]["Documentation"] = "https://example.org/new-docs"
    document, report = collect(previous=catalog, packages=[], get=lambda _: project)
    assert report["changed"]
    assert document["revision"] != catalog["revision"]
    assert document["plugins"][0]["docs_url"] == "https://example.org/new-docs"
    assert document["plugins"][0]["version"] == catalog["plugins"][0]["version"]


def test_data_station_is_the_comparison_source(catalog):
    """每轮从公开地址读取上一版，真实读取失败时不退化为空目录再覆盖数据站。"""
    calls = []

    def get(url, **kwargs):
        """记录实际公开读取地址并提供完整已发布目录。"""
        calls.append(url)
        return catalog

    assert (
        read_public_catalog("https://data.auto-mas.top/api/v1/files/a/b/c/download", get=get)
        == catalog
    )
    assert len(calls) == 1

    def failed(*args, **kwargs):
        """模拟数据站不可读，禁止将它当作首次初始化。"""
        raise OSError("offline")

    with pytest.raises(OSError):
        read_public_catalog("https://data.auto-mas.top/api/v1/files/a/b/c/download", get=failed)


def test_known_failure_does_not_publish_partial_catalog(catalog, project):
    """已收录插件网络失败时明确中止，即使新发现候选可读也不能发布残缺目录。"""
    original = deepcopy(catalog)

    def get(url):
        """让旧插件读取失败，另一个候选合法但不能掩盖旧插件丢失。"""
        if "/auto-mas-demo/" in url:
            raise OSError("offline")
        result = deepcopy(project)
        result["info"]["name"] = "unprefixed-adapter"
        result["info"]["keywords"] = "auto-mas-plugin,auto-mas-id:other_adapter"
        return result

    with pytest.raises(ValueError, match="incomplete"):
        collect(previous=catalog, packages=["unprefixed-adapter"], get=get)
    assert catalog == original


def test_explicit_removal_and_nonprefix_registration(catalog, project):
    """真实 404 可移除旧包；补录包不必使用命名前缀，仍须通过完整插件声明校验。"""

    def get(url):
        """模拟旧插件已下架、新插件通过补录被发现。"""
        if "/auto-mas-demo/" in url:
            raise HTTPError(url, 404, "removed", {}, None)
        result = deepcopy(project)
        result["info"]["name"] = "unprefixed-adapter"
        return result

    document, _ = collect(previous=catalog, packages=["unprefixed-adapter"], get=get)
    assert [entry["package_name"] for entry in document["plugins"]] == ["unprefixed-adapter"]


@pytest.mark.parametrize("failure", ["identity", "revision", "schema", "trigger"])
def test_invalid_document_is_rejected(catalog, failure):
    """拒绝身份冲突、摘要篡改、未知协议或带可执行配置字段的目录。"""
    document = deepcopy(catalog)
    if failure == "identity":
        document["plugins"].append(deepcopy(document["plugins"][0]))
    elif failure == "revision":
        document["plugins"][0]["version"] = "99.0"
    elif failure == "schema":
        document["schema_version"] = 1
    else:
        document["plugins"][0]["install"] = True
    with pytest.raises(ValueError):
        validate_catalog(document)


def test_rebuild_drops_removed_discovered_package(project):
    """明确下架的旧候选可从重建结果移除，新包仍按正常发布记录收录。"""

    def get(url):
        if "/removed/" in url:
            raise HTTPError(url, 404, "removed", {}, None)
        return project

    document, _ = collect(
        previous=None, known_packages=("removed",), packages=[project["info"]["name"]], get=get
    )
    assert [item["package_name"] for item in document["plugins"]] == [project["info"]["name"]]


def test_empty_unchanged_catalog_remains_valid():
    """明确空目录可以发布，后续无变化时保留同一目录，不根据年龄强制刷新。"""
    document = build_catalog([])
    assert build_catalog([], previous=document) == document


def test_collects_only_recent_published_notes_and_retains_them_on_failure(project):
    """以 PyPI 版本而非章节数量截取五版，保持身份、正文修正和网络失败时的日志一致。"""
    project["info"].update(version="1.10")
    project["info"]["project_urls"]["Changelog"] = "https://example.org/CHANGELOG.md"
    versions = ["1.1", "1.2", "1.3", "1.4", "1.5", "1.10", "2.0"]
    project["releases"] = {version: [{"filename": "dist.whl"}] for version in versions}
    source = """## [Unreleased]
pending
## [2.0]
future
## [1.10] - 2026-11-19
### Changed
- first
## [1.5]
- second
## [1.1]
too old
"""

    def refresh(previous, fetch):
        return collect(
            previous=previous,
            packages=[project["info"]["name"]],
            get=lambda _: project,
            fetch_notes=fetch,
        )[0]

    first = refresh(None, lambda _: source)
    expected = [
        {"version": "1.10", "body": "### Changed\n- first"},
        {"version": "1.5", "body": "- second"},
    ]
    assert first["plugins"][0]["changelog"] == expected
    edited = refresh(first, lambda _: source.replace("- second", "- corrected"))
    assert edited["revision"] != first["revision"]
    project["info"]["version"] = "2.0"

    def unavailable(_):
        raise OSError("offline")

    fallback = refresh(edited, unavailable)["plugins"][0]
    assert fallback["version"] == "2.0"
    assert fallback["changelog"] == [expected[0], {"version": "1.5", "body": "- corrected"}]
    del project["info"]["project_urls"]["Changelog"]
    assert refresh(edited, unavailable)["plugins"][0]["changelog"] == []
