import json
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from ai_trader.execution.paper import PaperBrokerAdapter
from ai_trader.paper_service import create_paper_app
from ai_trader.state_backup import backup_state, restore_state
from ai_trader.state_health import read_ledger


def fill_one(directory):
    with TestClient(create_paper_app(state_directory=directory), client=('127.0.0.1', 50000)) as client:
        run = client.post('/runs', json={'goal': 'fixture'}).json()['run_id']
        assert client.post(f'/runs/{run}/analyze').status_code == 200
        response = client.post(f'/runs/{run}/approve', headers={'X-Reviewer-Id': 'test'})
        assert response.status_code == 200
        return response.json()['order']


def test_lost_ledger_on_restart_is_not_ready_and_cannot_reset_account(tmp_path):
    fill_one(tmp_path)
    (tmp_path / 'paper-ledger.json').write_text('[]', encoding='utf-8')
    with TestClient(create_paper_app(state_directory=tmp_path), client=('127.0.0.1', 50000)) as client:
        assert client.get('/ready').status_code == 503
        assert client.get('/health').status_code == 200
        response = client.post('/runs', json={'goal': 'must not execute'})
        assert response.status_code == 503
        assert 'state' in response.json()['detail']


def test_sql_order_mismatch_is_not_ready_and_write_is_blocked(tmp_path):
    fill_one(tmp_path)
    with closing(sqlite3.connect(tmp_path / 'workflow.db')) as db:
        payload = json.loads(db.execute('SELECT payload FROM orders').fetchone()[0])
        payload['quantity'] = 200
        db.execute('UPDATE orders SET payload=?', (json.dumps(payload),))
        db.commit()
    with TestClient(create_paper_app(state_directory=tmp_path), client=('127.0.0.1', 50000)) as client:
        assert client.get('/ready').status_code == 503
        assert client.post('/runs', json={'goal': 'fixture'}).status_code == 503


@pytest.mark.parametrize('reader', [read_ledger, lambda directory: PaperBrokerAdapter(ledger_path=directory / 'paper-ledger.json')])
def test_duplicate_broker_order_identity_is_rejected(tmp_path, reader):
    order = fill_one(tmp_path)
    duplicate = {**order, 'proposal_id': 'another-proposal'}
    (tmp_path / 'paper-ledger.json').write_text(json.dumps([order, duplicate]), encoding='utf-8')
    with pytest.raises(ValueError, match='duplicate.*order'):
        reader(tmp_path)


def test_backup_rejects_missing_confirmed_broker_fact(tmp_path):
    source, backup = tmp_path / 'source', tmp_path / 'backup'
    fill_one(source)
    (source / 'paper-ledger.json').write_text('[]', encoding='utf-8')
    with pytest.raises(ValueError, match='confirmed SQL order'):
        backup_state(source, backup)
    assert not backup.exists()


def test_restore_rejects_cross_store_inconsistency_even_with_valid_hashes(tmp_path):
    import hashlib

    source, backup, target = (tmp_path / name for name in ('source', 'backup', 'target'))
    fill_one(source)
    backup_state(source, backup)
    ledger = backup / 'paper-ledger.json'
    ledger.write_text('[]', encoding='utf-8')
    manifest_path = backup / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['files']['paper-ledger.json'] = hashlib.sha256(ledger.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='confirmed SQL order'):
        restore_state(backup, target)
    assert not target.exists()


def test_unconfirmed_ledger_order_needs_a_known_reserved_proposal(tmp_path):
    from ai_trader.state_health import check_order_consistency

    order = fill_one(tmp_path)
    orphan = {**order, 'order_id': 'orphan-order', 'proposal_id': 'unknown-proposal'}
    (tmp_path / 'paper-ledger.json').write_text(json.dumps([order, orphan]), encoding='utf-8')
    with pytest.raises(ValueError, match='unconfirmed'):
        check_order_consistency(tmp_path, read_ledger(tmp_path))
