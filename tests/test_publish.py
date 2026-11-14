"""通过公开读取及上传边界验证不重复发布和失败恢复，不执行真实上传。"""

from copy import deepcopy

import pytest

from plugin_index.protocol import build_catalog, canonical_bytes
from plugin_index.publish import PublishError, publish

PUBLIC = "https://data.auto-mas.top/api/v1/files/auto-mas/Client/plugins/download"


def response(*, approved=True):
    """按数据站上传接口声明生成文件、公开路由和发布审核结果。"""
    return {
        "code": 0,
        "data": {
            "id": 7,
            "project_key": "auto-mas",
            "category_key": "Client",
            "file_key": "plugins",
            "published_version_no": 2 if approved else 1,
            "version": {"version_no": 2, "review_status": "approved" if approved else "pending"},
        },
    }


@pytest.mark.parametrize("different_timestamp", [False, True])
def test_unchanged_public_catalog_does_not_upload(catalog, different_timestamp):
    """目录内容一致时不追加版本，判断依据来自公开下载地址而非仓库快照。"""

    def upload(*args, **kwargs):
        """任何上传都表示无变化跳过契约被破坏。"""
        pytest.fail("unchanged catalog must not upload")

    live = deepcopy(catalog)
    if different_timestamp:
        live["updated_at"] = "2021-01-01T00:00:00+00:00"
    result = publish(
        canonical_bytes(catalog),
        file_id=7,
        public_url=PUBLIC,
        token="secret",
        get=lambda *args, **kwargs: live,
        upload=upload,
    )
    assert result["state"] == "unchanged"


@pytest.mark.parametrize("lost_response", [False, True])
def test_publish_and_retry_read_live_public_content(catalog, lost_response):
    """正常发布需公开回读；上传已生效但响应丢失时，重试通过现场读取跳过重复提交。"""
    entries = deepcopy(catalog["plugins"])
    entries[0]["version"] = "0.1.2"
    desired = build_catalog(entries, previous=catalog)
    live = [catalog]
    uploads = []

    def upload(url, token, content, note):
        """模拟数据站已经发布新版本，按场景丢失上传响应。"""
        uploads.append(url)
        live[0] = desired
        if lost_response:
            raise TimeoutError("response lost")
        return response()

    kwargs = dict(
        file_id=7,
        public_url=PUBLIC,
        token="secret",
        get=lambda *args, **kwargs: live[0],
        upload=upload,
        sleep=lambda _: None,
    )
    if lost_response:
        with pytest.raises(TimeoutError):
            publish(canonical_bytes(desired), **kwargs)
    else:
        assert publish(canonical_bytes(desired), **kwargs)["state"] == "published"
    assert publish(canonical_bytes(desired), **kwargs)["state"] == "unchanged"
    assert len(uploads) == 1


def test_pending_upload_is_not_reported_as_published(catalog):
    """接口受理但仍待审核时必须失败，不能把上传成功当作目录可供客户端读取。"""
    with pytest.raises(PublishError, match="not published"):
        publish(
            canonical_bytes(catalog),
            file_id=7,
            public_url=PUBLIC,
            token="secret",
            get=lambda *args, **kwargs: build_catalog([]),
            upload=lambda *args: response(approved=False),
        )


def test_unreadable_data_station_does_not_start_upload(catalog):
    """公开目录读取失败时不猜测内容有变化并盲目创建文件版本。"""

    def get(*args, **kwargs):
        """模拟数据站不可读。"""
        raise OSError("offline")

    def upload(*args):
        """预读失败后不得提交上传。"""
        pytest.fail("must not upload after an uncertain public read")

    with pytest.raises(OSError):
        publish(
            canonical_bytes(catalog),
            file_id=7,
            public_url=PUBLIC,
            token="secret",
            get=get,
            upload=upload,
        )
