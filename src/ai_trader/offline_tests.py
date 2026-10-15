"""离线回归入口：原 .env 只读为空，临时工作目录，禁止进程内外网连接。"""

import argparse
import builtins
import ipaddress
import json
import os
import socket
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

import httpx


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-isolation', action='store_true')
    args, pytest_args = parser.parse_known_args(argv)
    repository = Path(__file__).resolve().parents[2]
    real_env = (repository / '.env').resolve()
    real_open = builtins.open
    real_read = Path.read_text
    real_connect = socket.socket.connect
    real_http = httpx.HTTPTransport.handle_request
    real_async_http = httpx.AsyncHTTPTransport.handle_async_request
    original_cwd = Path.cwd()

    def guarded_open(path: Any, *arguments: Any, **kwargs: Any) -> Any:
        if isinstance(path, (str, os.PathLike)) and Path(path).resolve() == real_env:
            mode = arguments[0] if arguments else kwargs.get('mode', 'r')
            if mode.startswith('r') and '+' not in mode:
                return BytesIO(b'') if 'b' in mode else StringIO('')
            raise RuntimeError('original .env writes disabled in offline tests')
        return real_open(path, *arguments, **kwargs)

    def guarded_read(path: Path, *arguments: Any, **kwargs: Any) -> str:
        return '' if path.resolve() == real_env else real_read(path, *arguments, **kwargs)

    def local_connect(sock: socket.socket, address: Any) -> None:
        host = address[0] if isinstance(address, tuple) else ''
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host == 'localhost'
        if not local:
            raise RuntimeError('external network disabled in offline tests')
        return real_connect(sock, address)

    def check_http_target(request: httpx.Request) -> None:
        try:
            local = ipaddress.ip_address(request.url.host).is_loopback
        except ValueError:
            local = request.url.host == 'localhost'
        if not local:
            raise RuntimeError('external network disabled in offline tests')

    def local_http(transport: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        check_http_target(request)
        return real_http(transport, request)

    async def local_async_http(transport: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        check_http_target(request)
        return await real_async_http(transport, request)

    with TemporaryDirectory(prefix='ai-trader-offline-tests-') as temporary:
        # 既有契约演示按工作目录读取这个固定输入；只复制静态fixture，不复制状态或配置。
        fixture = Path(temporary) / 'evals/scenarios/core.json'
        fixture.parent.mkdir(parents=True)
        fixture.write_bytes((repository / 'evals/scenarios/core.json').read_bytes())
        with (
            patch.dict(os.environ, {
                'ANTHROPIC_AUTH_TOKEN': '', 'ANTHROPIC_API_KEY': '',
                'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                'NO_PROXY': '*',
                **{name: '' for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy')},
            }),
            patch.object(builtins, 'open', guarded_open),
            patch.object(Path, 'read_text', guarded_read),
            patch.object(socket.socket, 'connect', local_connect),
            patch.object(httpx.HTTPTransport, 'handle_request', local_http),
            patch.object(httpx.AsyncHTTPTransport, 'handle_async_request', local_async_http),
        ):
            try:
                os.chdir(temporary)
                if args.verify_isolation:
                    from ai_trader.settings import settings

                    assert not settings.anthropic_api_key
                    assert Path.cwd() == Path(temporary)
                    assert guarded_read(real_env) == ''
                    with socket.socket() as probe:
                        try:
                            probe.connect(('203.0.113.1', 443))
                        except RuntimeError:
                            pass
                        else:
                            raise AssertionError('external connection was allowed')
                    print(json.dumps({
                        'isolation': 'PASS', 'original_env': 'empty_read_only',
                        'working_directory': 'temporary', 'model_credentials': 'empty',
                        'external_network': 'blocked_in_process',
                    }))
                    return 0
                import pytest

                # 显式定位仓库配置，避免临时工作目录导致验收规则消失。
                return pytest.main([
                    '-c', str(repository / 'pyproject.toml'), str(repository / 'tests'),
                    *(pytest_args or ['-q', '--tb=short']),
                ])
            finally:
                os.chdir(original_cwd)


if __name__ == '__main__':
    raise SystemExit(main())
