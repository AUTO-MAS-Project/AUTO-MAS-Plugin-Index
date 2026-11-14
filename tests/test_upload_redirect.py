"""使用真实 curl 与本地 HTTPS 节点验证跨主机重定向后的上传认证。"""

import json
import ssl
import subprocess
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from plugin_index import publish as publisher
from plugin_index.protocol import build_catalog, canonical_bytes


def test_https_node_redirect_keeps_machine_auth_and_multipart(catalog, tmp_path, monkeypatch):
    """入口返回 307 到另一主机时，机器凭据、目录文件和变更说明均须到达实际上传节点。"""
    certificate, key = tmp_path / "server.crt", tmp_path / "server.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
        ],
        check=True,
        capture_output=True,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    live = [build_catalog([])]
    received = []
    token = "redirect-regression-token"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            """抑制测试服务器访问日志，认证只在测试内存中断言。"""

        def do_POST(self):
            """入口仅重定向；实际节点检查认证后保存上传内容并返回已发布结果。"""
            content = self.rfile.read(int(self.headers["Content-Length"]))
            if self.server is gateway:
                self.send_response(307)
                self.send_header("Location", f"https://localhost:{node.server_port}{self.path}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.headers.get("Authorization") != f"Bearer {token}":
                self.send_error(401)
                return
            message = BytesParser(policy=policy.default).parsebytes(
                b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + content
            )
            parts = {
                part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                for part in message.iter_parts()
            }
            live[0] = json.loads(parts["file"])
            received.append((live[0], parts["change_note"].decode()))
            response = json.dumps(
                {
                    "code": 0,
                    "data": {
                        "id": 7,
                        "project_key": "auto-mas",
                        "category_key": "Client",
                        "file_key": "plugins",
                        "published_version_no": 2,
                        "version": {"version_no": 2, "review_status": "approved"},
                    },
                }
            ).encode()
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    gateway = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    node = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    servers = (gateway, node)
    threads = []
    for server in servers:
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        threads.append(thread)
    run = subprocess.run

    def real_curl(command, **kwargs):
        """只给本地 TLS 请求提供测试 CA，其他 curl 行为仍由真实进程完成。"""
        return run([*command[:-1], "--cacert", str(certificate), command[-1]], **kwargs)

    monkeypatch.setattr(publisher.subprocess, "run", real_curl)
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")
    origin = f"https://127.0.0.1:{gateway.server_port}"
    try:
        result = publisher.publish(
            canonical_bytes(catalog),
            file_id=7,
            token=token,
            base_url=origin,
            public_url=f"{origin}/api/v1/files/auto-mas/Client/plugins/download",
            change_note="machine credential redirect test",
            get=lambda *args, **kwargs: live[0],
        )
        assert result["state"] == "published"
        assert received == [(catalog, "machine credential redirect test")]
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join()
