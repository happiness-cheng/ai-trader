import sqlite3

from fastapi.testclient import TestClient

from ai_trader.paper_service import create_paper_app
from ai_trader.state_health import check_sql


def test_readiness_closes_readonly_sqlite_connection(tmp_path, monkeypatch):
    with TestClient(create_paper_app(state_directory=tmp_path), client=('127.0.0.1', 50000)):
        pass
    connect = sqlite3.connect
    tracked = []
    class Connection:
        def __init__(self, *args, **kwargs):
            self.real = connect(*args, **kwargs)
            self.closed = False
            tracked.append(self)
        def execute(self, *args, **kwargs):
            return self.real.execute(*args, **kwargs)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return self.real.__exit__(*args)
        def close(self):
            self.closed = True
            self.real.close()
    monkeypatch.setattr(sqlite3, 'connect', Connection)
    check_sql(tmp_path)
    assert tracked and all(connection.closed for connection in tracked)
