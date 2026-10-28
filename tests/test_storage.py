import base64
import os
from pathlib import Path

import pytest

from evlet import Database, LocalFileStorage, SQLiteStorage, StorageError
from examples.playground.models import registry
from tests.test_business import secured, hashed, client


def store(secured, tmp_path):
    storage = LocalFileStorage(tmp_path / 'files')
    storage.initialize(secured.database)
    secured.business.storage = storage
    return storage


def test_existing_blob_attachments_remain_readable(secured, tmp_path):
    c = client(secured)
    c('POST', '/api/project', {'name': 'Old project'})
    assert c('POST', '/api/project/1/attachments', {'name': 'old.txt', 'content': 'b2xk'})[0] == 201
    storage = store(secured, tmp_path)
    assert c('GET', '/api/project/1/attachments/1')[1] == b'old'
    assert c('POST', '/api/project/1/attachments', {'name': 'new.txt', 'content': 'bmV3'})[0] == 201
    assert c('GET', '/api/project/1/attachments')[1]['items'] == [{'id': 1, 'name': 'old.txt', 'size': 3}, {'id': 2, 'name': 'new.txt', 'size': 3}]
    assert c('GET', '/api/project/1/attachments/2')[1] == b'new'
    assert c('DELETE', '/api/project/1/attachments/2')[0] == 204
    assert c('GET', '/api/project/1/attachments/2')[0] == 404
    assert storage.collect() == 1
    assert c('GET', '/api/project/1/attachments/1')[1] == b'old'


def test_file_rollback_and_parent_deletion_are_collectable(secured, tmp_path):
    storage = store(secured, tmp_path)
    with pytest.raises(RuntimeError):
        with secured.database.transaction() as tx:
            storage.add(tx, 'project', 1, 'rollback.txt', b'never committed')
            raise RuntimeError('abort')
    assert storage.collect() == 1
    c = client(secured); c('POST', '/api/project', {'name': 'Project'})
    c('POST', '/api/project/1/attachments', {'name': 'file.txt', 'content': 'YWJj'})
    assert storage.collect() == 0
    assert c('DELETE', '/api/project/1')[0] == 204
    assert storage.collect() == 1


def test_storage_roots_are_bound_to_one_database(secured, tmp_path):
    storage = store(secured, tmp_path)
    other = Database(tmp_path / 'other.sqlite3', registry); other.initialize()
    with pytest.raises(StorageError): LocalFileStorage(storage.root).initialize(other)
    assert storage.collect() == 0


def test_missing_or_symlink_files_and_invalid_keys_fail_closed(secured, tmp_path):
    storage = store(secured, tmp_path)
    c = client(secured); c('POST', '/api/project', {'name': 'Project'})
    c('POST', '/api/project/1/attachments', {'name': 'file.txt', 'content': 'YWJj'})
    with secured.database._connection() as db: row = db.execute('SELECT * FROM evlet_attachments').fetchone()
    path = storage.root / row['storage_key']; path.unlink()
    assert c('GET', '/api/project/1/attachments/1')[0] == 503
    secret = tmp_path / 'private.txt'; secret.write_text('private')
    path.symlink_to(secret)
    status, data, _ = c('GET', '/api/project/1/attachments/1')
    assert status == 503 and str(secret) not in str(data) and 'private' not in str(data)
    with secured.database.transaction() as tx: tx.connection.execute('UPDATE evlet_attachments SET storage_key=?', ('../private.txt',))
    assert c('GET', '/api/project/1/attachments/1')[0] == 503
    with pytest.raises(StorageError): SQLiteStorage().read(row)


def test_failed_file_write_does_not_commit_metadata(secured, tmp_path, monkeypatch):
    storage = store(secured, tmp_path)
    c = client(secured); c('POST', '/api/project', {'name': 'Project'})
    real_open = os.open
    def fail(path, flags, *args):
        if flags & os.O_CREAT: raise OSError('disk full')
        return real_open(path, flags, *args)
    monkeypatch.setattr(os, 'open', fail)
    assert c('POST', '/api/project/1/attachments', {'name': 'file.txt', 'content': 'YWJj'})[0] == 503
    assert c('GET', '/api/project/1/attachments')[1]['items'] == []


def test_root_and_marker_symlinks_rejected(secured, tmp_path):
    directory = tmp_path / 'real'; directory.mkdir()
    linked = tmp_path / 'linked'; linked.symlink_to(directory, target_is_directory=True)
    with pytest.raises(StorageError): LocalFileStorage(linked).initialize(secured.database)
    (directory / '.evlet-store').symlink_to(tmp_path / 'missing')
    with pytest.raises(StorageError): LocalFileStorage(directory).initialize(secured.database)


def test_file_adapter_rejects_other_database_transactions(secured, tmp_path):
    storage = store(secured, tmp_path)
    other = Database(tmp_path / 'other.sqlite3', registry); other.initialize()
    SQLiteStorage().initialize(other)
    with other.transaction() as tx:
        with pytest.raises(StorageError): storage.add(tx, 'project', 1, 'wrong.txt', b'wrong database')
    assert storage.collect() == 0


def test_corrupt_marker_returns_controlled_unavailability(secured, tmp_path):
    storage = store(secured, tmp_path)
    c = client(secured); c('POST', '/api/project', {'name': 'Project'})
    (storage.root / '.evlet-store').write_bytes(b'\xff')
    assert c('POST', '/api/project/1/attachments', {'name': 'file.txt', 'content': 'YWJj'})[0] == 503


def test_upgrade_preserves_original_attachment_table_and_bytes(tmp_path):
    database = Database(tmp_path / 'old.sqlite3', registry); database.initialize()
    with database.transaction() as tx:
        tx.connection.execute('CREATE TABLE evlet_attachments (id INTEGER PRIMARY KEY AUTOINCREMENT, model TEXT NOT NULL, record_id INTEGER NOT NULL, name TEXT NOT NULL, content BLOB NOT NULL)')
        tx.connection.execute('INSERT INTO evlet_attachments(model,record_id,name,content) VALUES (?,?,?,?)', ('project', 4, 'original.txt', b'original bytes'))
    storage = LocalFileStorage(tmp_path / 'files'); storage.initialize(database)
    with database._connection() as db:
        row = db.execute('SELECT * FROM evlet_attachments').fetchone()
        assert row['id'] == 1 and row['record_id'] == 4 and row['storage_key'] is None
        assert storage.read(row) == b'original bytes'


def test_collection_waits_for_upload_commit(secured, tmp_path):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event
    storage = store(secured, tmp_path)
    attempted = Event()
    def collect():
        attempted.set()
        return storage.collect()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with secured.database.transaction() as tx:
            file_id = storage.add(tx, 'project', 1, 'upload.txt', b'in flight')
            result = pool.submit(collect)
            assert attempted.wait(timeout=1)
            with pytest.raises(TimeoutError): result.result(timeout=0.05)
        assert result.result(timeout=2) == 0
    with secured.database._connection() as db:
        row = db.execute('SELECT * FROM evlet_attachments WHERE id=?', (file_id,)).fetchone()
        assert storage.read(row) == b'in flight'
