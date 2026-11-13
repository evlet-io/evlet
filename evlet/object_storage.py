"""Private S3-compatible attachments through an explicitly supplied SDK client."""

import hashlib
import re
import secrets

from . import qb
from .adapter import Column, TableDefinition
from .storage import MAX_FILE, SQLiteStorage, StorageError


OBJECT_STORE = TableDefinition('evlet_object_store', (
    Column('id', 'INTEGER', primary=True),
    Column('namespace', 'TEXT', required=True),
    Column('bucket', 'TEXT', required=True),
    Column('prefix', 'TEXT', required=True),
), checks=(qb.eq('id', 1),))


class S3Storage(SQLiteStorage):
    def __init__(self, client, *, bucket, prefix, tenant):
        if not isinstance(bucket, str) or not re.fullmatch('[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket):
            raise ValueError('Use an explicit bucket name')
        if not isinstance(prefix, str) or not re.fullmatch('[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*', prefix) or len(prefix) > 160:
            raise ValueError('Use a private object prefix without empty or relative segments')
        if not isinstance(tenant, str) or not re.fullmatch('[a-z][a-z0-9-]{0,47}', tenant):
            raise ValueError('Use an explicit tenant slug')
        self.client, self.bucket, self.tenant = client, bucket, tenant
        self.prefix = prefix + '/' + tenant + '/'
        self.database, self.namespace = None, None

    def initialize(self, database):
        super().initialize(database)
        with database.transaction() as tx:
            tx.connection.ensure_table(OBJECT_STORE)
            tx.connection.run(qb.insert('evlet_object_store', id=1, namespace=secrets.token_hex(32), bucket=self.bucket, prefix=self.prefix)
                              .on_conflict().do_nothing())
            row = tx.connection.run(qb.select('evlet_object_store').where(id=1)).one()
            if (row['bucket'], row['prefix']) != (self.bucket, self.prefix):
                raise StorageError('Object storage configuration differs from this database')
            self.namespace, self.database = row['namespace'], database
        try:
            try:
                self.client.put_object(Bucket=self.bucket, Key=self.prefix + '.evlet-store', Body=self.namespace.encode(), ContentLength=64, IfNoneMatch='*')
            except Exception as exc:
                code = str(getattr(exc, 'response', {}).get('Error', {}).get('Code', ''))
                if code not in ('PreconditionFailed', 'ConditionalRequestConflict', '409', '412'):
                    raise
            self._binding()
        except Exception as exc:
            raise StorageError('Private object storage is unavailable or belongs to another database') from exc

    def _binding(self):
        if self.database is None:
            raise StorageError('Object storage is not initialized')
        response = self.client.get_object(Bucket=self.bucket, Key=self.prefix + '.evlet-store')
        stream = response['Body']
        try:
            if stream.read(65) != self.namespace.encode():
                raise StorageError('Object prefix belongs to another database')
        finally:
            stream.close()

    def _key(self, key):
        if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{64}', key):
            raise StorageError('Invalid private object key')
        return self.prefix + 'objects/' + key

    def add(self, tx, model, record_id, name, content):
        if self.database is None or not self.database.owns(tx):
            raise StorageError('Attachment transaction belongs to another database')
        key = secrets.token_hex(32)
        try:
            self._binding()
            self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=content, ContentLength=len(content), ContentType='application/octet-stream', IfNoneMatch='*', Metadata={'sha256': hashlib.sha256(content).hexdigest()})
        except Exception as exc:
            raise StorageError('Private object upload failed') from exc
        return tx.connection.run(qb.insert('evlet_attachments', model=model, record_id=record_id, name=name, content=b'',
                                           storage_key=key, file_size=len(content), storage_kind='s3').returning_id()).id

    def read(self, row):
        if row['storage_key'] is None:
            return super().read(row)
        if row['storage_kind'] != 's3':
            raise StorageError('Attachment requires its original storage adapter')
        try:
            self._binding()
            response = self.client.get_object(Bucket=self.bucket, Key=self._key(row['storage_key']))
            stream = response['Body']
            try:
                if response['ContentLength'] != row['file_size'] or not 1 <= row['file_size'] <= MAX_FILE:
                    raise StorageError('Private object has an invalid size')
                content = stream.read(MAX_FILE + 1)
                if len(content) != row['file_size'] or hashlib.sha256(content).hexdigest() != response.get('Metadata', {}).get('sha256'):
                    raise StorageError('Private object is damaged')
                return content
            finally:
                stream.close()
        except Exception as exc:
            raise StorageError('Private object download failed') from exc

    def remove(self, key):
        """Delete one object whose metadata row is already gone, such as a replaced photo."""
        with self.database._connection() as connection:
            if connection.run(qb.select('evlet_attachments', qb.value(1)).where(qb.eq('storage_key', qb.value(key)))).one():
                return
        try:
            self._binding()
            self.client.delete_object(Bucket=self.bucket, Key=self._key(key))
        except Exception as exc:
            raise StorageError('Private object removal failed') from exc

    def collect(self):
        """Explicit collection with the same writer lock as attachment creation."""
        try:
            self._binding()
            with self.database.transaction() as tx:
                referenced = {row[0] for row in tx.connection.run(qb.select('evlet_attachments', 'storage_key').where(storage_kind='s3'))}
                arguments = {'Bucket': self.bucket, 'Prefix': self.prefix + 'objects/', 'MaxKeys': 1000}
                removed = 0
                while True:
                    response = self.client.list_objects_v2(**arguments)
                    for item in response.get('Contents', []):
                        full_key = item['Key']
                        key = full_key.removeprefix(self.prefix + 'objects/')
                        if re.fullmatch('[0-9a-f]{64}', key) and self._key(key) == full_key and key not in referenced:
                            self.client.delete_object(Bucket=self.bucket, Key=full_key)
                            removed += 1
                    if not response.get('IsTruncated'):
                        return removed
                    arguments['ContinuationToken'] = response['NextContinuationToken']
        except Exception as exc:
            raise StorageError('Private object collection failed') from exc
