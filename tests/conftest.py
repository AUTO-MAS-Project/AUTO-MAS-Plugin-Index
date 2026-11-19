"""使用固定线协议样例，隔离外部源与数据站副作用。"""

import json
from pathlib import Path

import pytest


@pytest.fixture
def catalog():
    """加载与主程序共用的目录 v2 样例，校验双方对完整元信息和内容摘要的理解。"""
    return json.loads(
        (Path(__file__).parent / "fixtures/catalog-v2.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def project(catalog):
    """提供与线协议样例对应的 PyPI 公开元数据，不安装或导入候选插件。"""
    entry = catalog["plugins"][0]
    return {
        "info": {
            "name": entry["package_name"],
            "version": entry["version"],
            "keywords": "auto-mas-plugin,auto-mas-id:demo_adapter",
            "author": entry["author"],
            "license_expression": entry["license"],
            "requires_dist": entry["requires_dist"],
            "project_urls": {
                "Documentation": entry["docs_url"],
                "Homepage": entry["homepage"],
                "Source_Code": entry["repository"],
            },
            "description": "<!-- auto-mas:i18n\n"
            + json.dumps(
                {key: entry[key] for key in ("display_name_i18n", "description_i18n", "tags_i18n")},
                ensure_ascii=False,
            )
            + "\n-->",
        }
    }
