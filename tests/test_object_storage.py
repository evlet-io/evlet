import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from urllib.parse import parse_qs, unquote, urlsplit
from xml.sax.saxutils import escape

import pytest

from evlet import Database, LocalFileStorage, S3Storage, StorageError
from examples.playground.models import registry
from test_business import secured, hashed, client


@pytest.fixture
def s3_server():
    boto3 = pytest.importorskip('boto3')
    from botocore.config import Config
    objects, requests = {}, []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def reply(self, status, content=b'', headers=None):
            self.send_response(status)
            self.send_header('Content-Length', str(len(content)))
            for key, value in (headers or {}).items(): self.send_header(key, value)
            self.end_headers(); self.wfile.write(content)
        def key(self): return unquote(urlsplit(self.path).path).removeprefix('/private-bucket/')
        def do_PUT(self):
            requests.append((self.command, self.path, dict(self.headers)))
            content = self.rfile.read(int(self.headers['Content-Length']))
            if self.headers.get('If-None-Match') == '*' and self.key() in objects:
                self.reply(412, b'<Error><Code>PreconditionFailed</Code></Error>'); return
            objects[self.key()] = (content, self.headers.get('x-amz-meta-sha256'))
            self.reply(200, headers={'ETag': '"test"'})
        def do_GET(self):
            requests.append((self.command, self.path, dict(self.headers)))
            query = parse_qs(urlsplit(self.path).query)
            if query.get('list-type') == ['2']:
                prefix = query['prefix'][0]
                body = '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>false</IsTruncated>'
                for key in sorted(objects):
                    if key.startswith(prefix): body += '<Contents><Key>' + escape(key) + '</Key></Contents>'
                self.reply(200, (body + '</ListBucketResult>').encode()); return
            if self.key() not in objects:
                self.reply(404, b'<Error><Code>NoSuchKey</Code></Error>'); return
            content, digest = objects[self.key()]
            self.reply(200, content, {'x-amz-meta-sha256': digest} if digest else {})
        def do_DELETE(self):
            requests.append((self.command, self.path, dict(self.headers)))
            objects.pop(self.key(), None); self.reply(204)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    sdk = boto3.client('s3', endpoint_url=f'http://127.0.0.1:{server.server_port}', region_name='us-east-1', aws_access_key_id='local-test-key', aws_secret_access_key='local-test-secret', config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}, retries={'max_attempts': 0}, connect_timeout=1, read_timeout=2, request_checksum_calculation='when_required', response_checksum_validation='when_required'))
    try: yield sdk, objects, requests
    finally: sdk.close(); server.shutdown(); thread.join(timeout=3); server.server_close()


def storage(secured, s3_server, tenant='alpha'):
    sdk, _, _ = s3_server
    result = S3Storage(sdk, bucket='private-bucket', prefix='evlet', tenant=tenant)
    result.initialize(secured.database)
    secured.business.storage = result
    return result


def test_signed_private_upload_download_delete_and_collection(secured, s3_server):
    store = storage(secured, s3_server)
    c = client(secured); pid = c('POST', '/api/project', {'title': 'Files'})[1]['id']
    assert c('POST', f'/api/project/{pid}/attachments', {'name': 'private.txt', 'content': 'cHJpdmF0ZQ=='})[0] == 201
    assert c('GET', f'/api/project/{pid}/attachments/1')[1] == b'private'
    assert store.collect() == 0
    viewer = client(secured, 'viewer')
    assert viewer('DELETE', f'/api/project/{pid}/attachments/1')[0] == 403
    assert c('DELETE', f'/api/project/{pid}/attachments/1')[0] == 204
    assert store.collect() == 1
    assert set(s3_server[1]) == {'evlet/alpha/.evlet-store'}
    assert all(headers['Authorization'].startswith('AWS4-HMAC-SHA256') for _, _, headers in s3_server[2])
    assert all(path.startswith('/private-bucket') for _, path, _ in s3_server[2])


def test_prefix_binding_and_cross_database_transactions(secured, s3_server, tmp_path):
    store = storage(secured, s3_server)
    store.initialize(secured.database)
    other = Database(tmp_path / 'other.sqlite3', registry); other.initialize()
    with pytest.raises(StorageError):
        S3Storage(s3_server[0], bucket='private-bucket', prefix='evlet', tenant='alpha').initialize(other)
    with other.transaction() as tx:
        with pytest.raises(StorageError): store.add(tx, 'project', 1, 'wrong', b'wrong')
    beta = S3Storage(s3_server[0], bucket='private-bucket', prefix='separate', tenant='beta')
    # A database's bucket/prefix cannot silently change after binding.
    with pytest.raises(StorageError): beta.initialize(secured.database)


def test_rollback_orphans_corruption_and_original_adapter_guard(secured, s3_server, tmp_path):
    store = storage(secured, s3_server)
    with pytest.raises(RuntimeError):
        with secured.database.transaction() as tx:
            store.add(tx, 'project', 1, 'rollback', b'rollback')
            raise RuntimeError('abort')
    assert store.collect() == 1
    c = client(secured); pid = c('POST', '/api/project', {'title': 'Files'})[1]['id']
    c('POST', f'/api/project/{pid}/attachments', {'name': 'data', 'content': 'YWJj'})
    with secured.database._connection() as db: row = db.execute('SELECT * FROM evlet_attachments').fetchone()
    local = LocalFileStorage(tmp_path / 'files'); local.initialize(secured.database)
    with pytest.raises(StorageError): local.read(row)
    key = store._key(row['storage_key'])
    s3_server[1][key] = (b'bad', hashlib.sha256(b'abc').hexdigest())
    status, data, _ = c('GET', f'/api/project/{pid}/attachments/1')
    assert status == 503 and 'private-bucket' not in str(data)
    del s3_server[1][key]
    assert c('GET', f'/api/project/{pid}/attachments/1')[0] == 503


def test_old_blob_bytes_work_with_object_storage(secured, s3_server):
    c = client(secured); pid = c('POST', '/api/project', {'title': 'Old'})[1]['id']
    c('POST', f'/api/project/{pid}/attachments', {'name': 'old', 'content': 'b2xk'})
    storage(secured, s3_server)
    assert c('GET', f'/api/project/{pid}/attachments/1')[1] == b'old'
