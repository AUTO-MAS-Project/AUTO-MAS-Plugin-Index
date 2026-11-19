# AUTO-MAS Plugin Index

为 AUTO-MAS 数据站生成可直接读取的插件目录。客户端只需一次目录请求；PyPI 和镜像逐包检索保留在主程序作为兜底。

## 数据流

每次执行直接读取 `AUTOMAS_PUBLIC_URL` 的公开目录，取得已收录包及上次有效内容，再向 PyPI 查询发行元信息。默认每 30 分钟检查已知插件，每天 UTC 00:07（北京时间 08:07）通过 PyPI Simple API 的 JSON 总索引扫描一次新候选。GitHub 定时任务可能延迟；Actions 可手动选择 `discover` 立即扫描。

`config/packages.json` 补录包名，可使用任意名称；准入依据始终是发行元数据中的 `auto-mas-plugin`、唯一 `auto-mas-id:` 和 README 开头完整的 `auto-mas:i18n` 声明。不会安装或执行插件。随主程序提供的 core SDK 不进入市场目录。

目录包含包名、插件标识、版本、文档/主页/仓库地址、作者、许可证、依赖和名称/简介/标签的多语言映射，协议见 [schema/plugin-index.schema.json](schema/plugin-index.schema.json)。每项的 `changelog` 保存目标版本及之前最近五个 PyPI 发行版本内的说明。采集器从项目元数据的 `Changelog` 链接读取 Markdown，以 `## [版本] - 日期` 分段；文件读取失败时仅保留同一插件且仍在窗口内的旧说明，缺少链接则留空。当前版本为 `schema_version=2`。

`revision` 是协议版本和规范化插件内容的 SHA-256，不包含轮询时间。版本号未变但链接、简介或语言资源变化也会生成新摘要。内容相同时复用公开文档及其 `updated_at`，不创建新数据站版本。目录不因长期未更新而过期。

已收录或明确补录的插件读取失败会中止本轮，旧公开目录保持可用；无法识别的未知候选只记录并跳过。已收录且未固定补录的包在 PyPI 明确返回 404 时才移除。数据站读取失败不能当作空目录覆盖；首次公开地址返回 404 可以初始化。重复包名、插件标识冲突及不完整元信息均拒绝发布。

仓库只维护程序、配置、协议和测试。每轮生成文件属于工作目录临时产物，不提交到任何 Git 分支。上传失败后重新执行时，仍从数据站现场读取并比较；如果上次已发布但响应丢失，就直接跳过重复上传。

## 本地使用

Python 3.12+、uv；发布另需系统 curl。依赖由 uv.lock 锁定。

```powershell
uv sync --locked
uv run plugin-index --public-url https://data.auto-mas.top/api/v1/files/实际项目/实际分类/实际文件/download
uv run plugin-index --discover
uv run plugin-index-publish
```

输出默认位于忽略的 `api/v1/plugins.json`，结果报告为忽略的 `collect-report.json`。Actions 将本轮结果保留为 7 天的 `plugin-catalog` 产物供人工查看，下一轮不读取这些产物。本地先设置环境变量，第二条命令才会使用同一公开目录：

```powershell
$env:AUTOMAS_PUBLIC_URL = '实际固定公开下载地址'
$env:AUTOMAS_FILE_ID = '实际文件数字 ID'
# AUTOMAS_TOKEN 从安全的本地环境或 CI Secret 注入。
```

尚无数据站目标文件时，可不设置公开地址，执行 `uv run plugin-index --discover` 生成预览，不会上传。已有文件时必须使用真实公开地址，不能省略它来绕开旧目录读取失败。

## GitHub Actions 与数据站配置

首次切换到 v2 时，手动运行“更新插件目录”并选择 `rebuild`。工作流校验线上目录摘要，从已收录包名和 PyPI 发现结果全量生成 v2，上传为原文件的新版本，并在同一公开地址回读。重建期间若线上目录已变化，重新执行即可。

1. 尚未配置公开地址时，先运行一次 `preview`，从 Actions 的 `plugin-catalog` 产物中取出 `api/v1/plugins.json`，用它在数据站创建文件，再取得数字文件 ID 和不带版本参数的固定公开下载地址。首传内容必须是符合协议的插件目录，不能使用其他业务的 JSON 或任意占位文件。
2. Repository Variables 设置 `AUTOMAS_FILE_ID`、`AUTOMAS_PUBLIC_URL`。
3. Repository Secret 设置 `AUTOMAS_TOKEN`，使用有权发布该文件版本的 Access Key。上传端会验证实际审核通过并公开，待审核不会报告成功。
4. 手动运行“更新插件目录”，选择 `preview` 只采集，`refresh` 刷新已知包并发布，`discover` 同时发现新包并发布。首次没有公开目录时自动扫描新候选。

公开地址未配置时，定时任务不运行；手动执行只生成预览。地址已配置但缺少上传凭据或文件 ID 会明确失败。整个工作流只有 `contents: read` 权限，没有生成数据提交、状态分支或自动推送。

若采集阶段提示 `invalid catalog envelope`，先检查公开下载内容是否真的是插件目录：应包含 `schema_version`、`revision`、`updated_at`、`plugins`。维护状态文件中的 `generated_at`、`games` 不属于此协议。已有目标文件误传时，人工将正确目录提交为新版本再重跑；此时尚未调用机器凭据上传，不应放宽校验或把错误内容当作空目录覆盖。

发布复用 Maintenance 的数据站上传与公开回读流程：`POST /api/v1/user/files/{file_id}/versions`，multipart 包含 `file` 和 `change_note`，认证头经 curl 标准输入提供，不写入参数和日志。公开内容一致时跳过上传；POST 不因网络错误自动重试；409 只有公开回读一致才视为成功。上传后回读客户端使用的同一固定地址，确认目录真的可用。

上传入口 `data.auto-mas.top` 会重定向到数据节点 `data2.auto-mas.top`。与 Maintenance 一致，curl 使用 `--location-trusted` 在可信数据站的 HTTPS 重定向中保留机器认证头；只有 `--location` 会使跨主机认证头丢失，导致实际节点返回 401。

## 主程序接入

主程序默认目录地址为 `https://data.auto-mas.top/api/v1/files/auto-mas/Client/plugins/download`，可通过进程变量 `AUTO_MAS_PLUGIN_INDEX_URL` 覆写。目录请求默认 5 秒超时；成功后不再访问 PyPI 或镜像，支持有效 ETag/304 缓存，内容相同保留现有目录条目身份。显式将该变量设为空可仅使用原检索兜底；远端源全部失败时保留旧目录并报错。

安装仍由宿主 uv 完成，目录文件不替代安装源、依赖预检或插件启用流程。

## 验证

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

测试只替换外部 HTTP/上传边界，覆盖跨仓库目录样例、元信息修正、现场比较、部分失败、无变化零上传、上传响应丢失后的重试、待审核结果及损坏文档。无真实上传凭据也能运行。

跨主机重定向回归使用真实 curl 与两个本地 HTTPS 节点，需要系统 curl、openssl；测试证书和密钥仅在临时目录生成。Windows 可先将 Git 的 `usr/bin` 加入 PATH，例如 `$env:PATH='C:\Program Files\Git\usr\bin;'+$env:PATH`。该测试不连接外部数据站，也不使用真实机器凭据。

元数据解析与主程序 `app/plugin/runtime/metadata.py` 保持相同声明语义；线协议与宿主 `app/plugin/runtime/catalog_protocol.py` 通过相同目录 v2 样例验证，变更协议时两边一起验证。上传流程参考 [AUTO-MAS-Maintenance](https://github.com/AUTO-MAS-Project/AUTO-MAS-Maintenance)，保留其成功结果回读机制；状态比较直接读取数据站。
