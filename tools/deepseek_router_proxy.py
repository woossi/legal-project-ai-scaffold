#!/usr/bin/env python3
"""모델명으로 목적지를 가르는 라우팅 프록시.

`ANTHROPIC_BASE_URL` 이 프로세스당 하나뿐이라는 제약을 우회한다. 이 프록시를 그 자리에
놓으면 요청 본문의 `model` 값을 보고 목적지를 정하므로, 서브에이전트 frontmatter 의
`model: deepseek-v4-flash` 가 그대로 동작한다.

DeepSeek 이 Anthropic 포맷을 그대로 받으므로 본문은 손대지 않는다. 바꾸는 것은
목적지 호스트와 인증 헤더뿐이다.

    router_proxy.py [--port 8787]
    ANTHROPIC_BASE_URL=http://127.0.0.1:8787 claude

사용자 승인 아래 만든 것이다 (2026-08-12). 세션의 모든 LLM 트래픽이 이 프로세스를
지나므로, 여기서 죽으면 Claude 작업도 같이 죽는다.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEEPSEEK_UPSTREAM = "https://api.deepseek.com/anthropic"
ANTHROPIC_UPSTREAM = "https://api.anthropic.com"
KEY_FILE = Path.home() / ".claude" / ".deepseek.env"

# 홉 단위 헤더는 그대로 넘기면 안 된다. Host 는 목적지에 맞게 다시 붙는다.
STRIP_HEADERS = {"host", "content-length", "connection", "keep-alive",
                 "transfer-encoding", "upgrade", "proxy-authorization"}

DEEPSEEK_KEY = ""


def load_deepseek_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if key:
        return key
    if not KEY_FILE.exists():
        sys.exit(f"키 파일 없음: {KEY_FILE}")
    for line in KEY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip().removeprefix("export ").strip()
        name, _, value = line.partition("=")
        if name.strip() == "DEEPSEEK_API_KEY":
            return value.strip().strip("'\"")
    sys.exit(f"{KEY_FILE} 에 DEEPSEEK_API_KEY 가 없다")


class Router(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):  # 기본 접근 로그를 끈다. 아래에서 직접 찍는다.
        pass

    def _note(self, msg: str) -> None:
        # 자격증명은 절대 찍지 않는다. 모델명과 목적지만 남긴다.
        sys.stderr.write(f"[router] {msg}\n")
        sys.stderr.flush()

    def _send_payload(self, status: int, payload: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _relay(self, method: str) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else b""

        model = ""
        if body:
            try:
                model = (json.loads(body) or {}).get("model", "") or ""
            except (json.JSONDecodeError, AttributeError, TypeError):
                pass

        to_deepseek = model.startswith("deepseek")
        url = (DEEPSEEK_UPSTREAM if to_deepseek else ANTHROPIC_UPSTREAM) + self.path

        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in STRIP_HEADERS}
        if to_deepseek:
            # 구독 OAuth 토큰을 DeepSeek 에 넘기지 않는다.
            for name in [n for n in headers if n.lower() in ("authorization", "x-api-key")]:
                headers.pop(name)
            headers["x-api-key"] = DEEPSEEK_KEY
            headers.setdefault("anthropic-version", "2023-06-01")

        self._note(f"{method} {self.path} model={model or '-'} "
                   f"-> {'deepseek' if to_deepseek else 'anthropic'}")

        req = urllib.request.Request(url, data=body or None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=900) as resp:
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    if k.lower() not in STRIP_HEADERS:
                        self.send_header(k, v)
                self.send_header("Connection", "close")
                self.end_headers()
                # SSE 스트리밍을 끊지 않도록 청크 단위로 흘린다.
                while chunk := resp.read(8192):
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except urllib.error.HTTPError as e:
            payload = e.read()
            self._note(f"upstream {e.code} <- {'deepseek' if to_deepseek else 'anthropic'}")
            self._send_payload(e.code, payload,
                               e.headers.get("Content-Type", "application/json"))
        except Exception as e:  # 프록시가 죽으면 세션 전체가 죽는다. 502 로 되돌린다.
            self._note(f"proxy error: {e}")
            payload = json.dumps({"type": "error",
                                  "error": {"type": "proxy_error",
                                            "message": str(e)}}).encode()
            self._send_payload(502, payload, "application/json")

    def do_POST(self):
        self._relay("POST")

    def do_GET(self):
        self._relay("GET")


def main() -> int:
    global DEEPSEEK_KEY
    ap = argparse.ArgumentParser(description="모델명 기반 Anthropic/DeepSeek 라우팅 프록시")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()

    DEEPSEEK_KEY = load_deepseek_key()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Router)
    sys.stderr.write(f"[router] 127.0.0.1:{args.port} — deepseek* 는 DeepSeek, "
                     f"그 밖은 Anthropic 으로 보낸다\n")
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
