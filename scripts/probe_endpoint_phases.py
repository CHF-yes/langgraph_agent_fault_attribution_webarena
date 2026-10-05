#!/usr/bin/env python3
"""逐阶段定位"首次调用慢"到底慢在哪：DNS / TCP / TLS / 响应头 / 首包 / 结束。

不用 SDK，直接发原始 HTTP 请求，每个阶段完成即打印（flush），便于实时读日志。
只打印状态行与选定的响应头，不打印 Authorization 或密钥。
"""
from __future__ import annotations

import json
import socket
import ssl
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from standard_agent.config import settings  # noqa: E402


def probe(label: str, host: str, path: str, key: str, model: str,
          extra: dict | None = None, families=(socket.AF_INET, socket.AF_INET6)) -> None:
    print(f"\n=== {label}  host={host}", flush=True)
    try:
        t0 = time.time()
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        addresses = []
        for info in infos:
            if info[0] in families and info[4][0] not in [a[1][0] for a in addresses]:
                # info[4] 对 v6 是 4 元组，create_connection 只接受 (host, port)
                addresses.append((info[0], info[4][:2]))
        print(f"  DNS           {time.time()-t0:7.3f}s  → "
              f"{[('v6' if f==socket.AF_INET6 else 'v4', a[0]) for f, a in addresses][:3]}", flush=True)
    except Exception as exc:
        print(f"  DNS FAILED    {type(exc).__name__}: {exc}", flush=True)
        return

    for family, address in addresses[:2]:
        tag = "v6" if family == socket.AF_INET6 else "v4"
        t0 = time.time()
        try:
            sock = socket.create_connection(address, 10)
            print(f"  TCP({tag})       {time.time()-t0:7.3f}s  {address[0]}", flush=True)
        except Exception as exc:
            print(f"  TCP({tag}) FAIL  {time.time()-t0:7.3f}s  {type(exc).__name__}: {exc}", flush=True)
            continue
        t0 = time.time()
        try:
            tls = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            print(f"  TLS({tag})       {time.time()-t0:7.3f}s  {tls.version()}", flush=True)
        except Exception as exc:
            print(f"  TLS({tag}) FAIL  {time.time()-t0:7.3f}s  {type(exc).__name__}: {exc}", flush=True)
            sock.close()
            continue

        body = json.dumps({
            "model": model, "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 8, "stream": True, **(extra or {}),
        }).encode()
        head = (f"POST {path} HTTP/1.1\r\nHost: {host}\r\n"
                f"Authorization: Bearer {key}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode()
        t0 = time.time()
        tls.sendall(head + body)
        buffer = b""
        try:
            while b"\r\n\r\n" not in buffer:
                chunk = tls.recv(4096)
                if not chunk:
                    break
                buffer += chunk
        except Exception as exc:
            print(f"  headers FAIL  {time.time()-t0:7.3f}s  {type(exc).__name__}", flush=True)
            tls.close()
            continue
        print(f"  headers       {time.time()-t0:7.3f}s  "
              f"{buffer.split(chr(13).encode(), 1)[0].decode(errors='replace')}", flush=True)
        first = None
        total = len(buffer)
        while True:
            try:
                chunk = tls.recv(65536)
            except Exception as exc:
                print(f"  stream FAIL   {time.time()-t0:7.3f}s  {type(exc).__name__}", flush=True)
                break
            if not chunk:
                break
            if first is None:
                first = time.time() - t0
            total += len(chunk)
        print(f"  first_body    {('%.3f' % first) if first is not None else 'n/a':>7}s  "
              f"end {time.time()-t0:7.3f}s  bytes={total}", flush=True)
        tls.close()
        break


def main() -> int:
    key = settings.get_model_profile("qwen38_flash").api_key
    deep_key = settings.get_model_profile("deepseek_v41_flash").api_key
    path = "/compatible-mode/v1/chat/completions"
    import time as _t
    print("\n--- 空闲 20s 后打专用实例（预期：响应头等到 ~270s 才到，证明是服务端拉起）", flush=True)
    _t.sleep(20)
    probe("qwen 专属实例(空闲后, v4)", "maas.qianwenaiapi.com", path, key, "qwen3.8-flash",
          families=(socket.AF_INET,))
    probe("qwen 共享平台(v4 强制)", "dashscope.aliyuncs.com", path, key, "qwen3.8-flash",
          families=(socket.AF_INET,))
    probe("deepseek 官方（对照）", "api.deepseek.com", "/chat/completions",
          deep_key, "deepseek-flash")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
