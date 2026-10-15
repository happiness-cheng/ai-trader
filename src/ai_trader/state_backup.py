"""停止 Paper 服务后备份/恢复；恢复只写空目标，不覆盖现有数据。"""
import argparse
import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from ai_trader.paper_service import _single_writer
from ai_trader.state_health import check_order_consistency, check_sql, read_ledger

ALLOWED = frozenset({'workflow.db', 'paper-ledger.json'})


def _hash(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _absolute(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('absolute nonsymlink directory required')
    return path.resolve()


def _empty(path: Path) -> None:
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError('destination must be empty')


def _safe_file(directory: Path, name: str) -> Path:
    path = directory / name
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(directory):
        raise ValueError('invalid backup file')
    return path


def backup_state(source: Path, destination: Path) -> None:
    source, destination = _absolute(source), _absolute(destination)
    if not source.is_dir() or destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError('backup requires separate existing source directory')
    _empty(destination)
    with _single_writer(source):
        database = _safe_file(source, 'workflow.db')
        check_sql(source, integrity=True)
        check_order_consistency(source, read_ledger(source))
        ledger = source / 'paper-ledger.json'
        if ledger.exists():
            _safe_file(source, ledger.name)
        # 排他创建，覆盖预检查后其他进程创建目标的竞态。
        destination.mkdir(parents=True, exist_ok=False)
        # SQLite backup API 包含尚在 WAL 的已提交数据；原库只读。
        with (closing(sqlite3.connect(f'{database.as_uri()}?mode=ro', uri=True)) as src,
              closing(sqlite3.connect(destination / 'workflow.db')) as dst):
            src.backup(dst)
        files = {'workflow.db': _hash(destination / 'workflow.db')}
        if ledger.exists():
            shutil.copyfile(ledger, destination / ledger.name)
            files[ledger.name] = _hash(destination / ledger.name)
        with (destination / 'manifest.json').open('x', encoding='utf-8') as stream:
            json.dump({'version': 1, 'files': files}, stream, sort_keys=True)


def restore_state(backup: Path, destination: Path) -> None:
    backup, destination = _absolute(backup), _absolute(destination)
    _empty(destination)
    if destination.is_relative_to(backup) or backup.is_relative_to(destination):
        raise ValueError('restore requires separate directories')
    manifest_file = _safe_file(backup, 'manifest.json')
    if manifest_file.stat().st_size > 65536:
        raise ValueError('invalid backup manifest size')
    manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
    if not isinstance(manifest, dict) or manifest.get('version') != 1:
        raise ValueError('invalid backup manifest')
    files = manifest.get('files')
    if not isinstance(files, dict) or 'workflow.db' not in files or not set(files) <= ALLOWED:
        raise ValueError('invalid backup manifest files')
    if {path.name for path in backup.iterdir()} != set(files) | {'manifest.json'}:
        raise ValueError('invalid backup manifest directory')
    for name, expected in files.items():
        if not isinstance(expected, str) or _hash(_safe_file(backup, name)) != expected:
            raise ValueError('backup hash mismatch')
    check_sql(backup, integrity=True)
    check_order_consistency(backup, read_ledger(backup))
    # 所有输入校验在创建目标之前完成。哈希只证明完整性，不证明备份来源可信。
    with _single_writer(destination):
        if any(path.name != '.paper-service.lock' for path in destination.iterdir()):
            raise ValueError('destination must be empty')
        for name in files:
            with (destination / name).open('xb') as stream, (backup / name).open('rb') as src:
                shutil.copyfileobj(src, stream)
            if _hash(destination / name) != files[name]:
                raise ValueError('restored file hash mismatch')
        check_sql(destination, integrity=True)
        check_order_consistency(destination, read_ledger(destination))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('backup', 'restore'))
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        (backup_state if args.operation == 'backup' else restore_state)(args.source, args.destination)
    except (OSError, ValueError, RuntimeError, sqlite3.Error):
        parser.error('state operation failed; service must be stopped and destination empty')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
