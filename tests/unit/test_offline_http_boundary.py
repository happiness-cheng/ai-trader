"""本机代理不能绕过离线测试的外部目标限制；假代理绝不转发。"""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx
import pytest


@contextmanager
def local_proxy():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(302 if self.path.endswith('/redirect') else 200)
            if self.path.endswith('/redirect'):
                self.send_header('Location', 'http://203.0.113.1/fixture')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}', requests
        finally:
            server.shutdown()
            thread.join(timeout=2)


@pytest.mark.parametrize('redirect', [False, True])
def test_sync_external_target_is_blocked_before_local_proxy(redirect):
    with local_proxy() as (proxy, requests), httpx.Client(proxy=proxy, timeout=.5) as client:
        with pytest.raises(RuntimeError, match='external network disabled'):
            client.get(f'{proxy}/redirect' if redirect else 'http://203.0.113.1/fixture', follow_redirects=True)
        assert requests == ([f'{proxy}/redirect'] if redirect else [])


@pytest.mark.parametrize('redirect', [False, True])
def test_async_external_target_is_blocked_before_local_proxy(redirect):
    with local_proxy() as (proxy, requests):
        async def invoke():
            async with httpx.AsyncClient(proxy=proxy, timeout=.5) as client:
                with pytest.raises(RuntimeError, match='external network disabled'):
                    await client.get(f'{proxy}/redirect' if redirect else 'http://203.0.113.1/fixture', follow_redirects=True)
        asyncio.run(invoke())
        assert requests == ([f'{proxy}/redirect'] if redirect else [])
