"""核验 wheel 只含 runtime 包、元数据，且 Python 源码与工作区一致。"""
import argparse
import hashlib
from pathlib import Path
from zipfile import ZipFile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wheel', type=Path)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / 'src'
    expected = {str(path.relative_to(source)).replace('\\', '/'): path
                for path in (source / 'ai_trader').rglob('*.py')}
    with ZipFile(args.wheel) as archive:
        names = set(archive.namelist())
        actual = {name for name in names if name.endswith('.py')}
        if actual != set(expected):
            raise SystemExit('wheel Python members differ from current runtime source')
        for name, path in expected.items():
            if archive.read(name) != path.read_bytes():
                raise SystemExit(f'wheel source mismatch: {name}')
        for name in names:
            if not (name.startswith('ai_trader/') or '.dist-info/' in name):
                raise SystemExit(f'unexpected wheel member: {name}')
            if Path(name).name.startswith('.env') or name.endswith(('.db', '.sqlite', '.safetensors')):
                raise SystemExit('sensitive state in wheel')
    with args.wheel.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    print(f'PASS: {len(expected)} source files match; no business state; SHA256={digest}')


if __name__ == '__main__':
    main()
