"""Seal small secrets with the instance's secret key, using AES-GCM.

The key is 32 random bytes in instances/<name>/secret.key, created on first use with mode 0600.
AES-GCM comes from the optional `cryptography` package; without it, seal and open raise
SecretsUnavailable and nothing that needs a secret can be saved.
"""

import os
from pathlib import Path
import secrets

VERSION = b'\x01'


class SecretsUnavailable(RuntimeError):
    pass


def available(key_path):
    """Whether secrets can be sealed: a key path is configured and cryptography is installed."""
    if key_path is None:
        return False
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
    except ImportError:
        return False
    return True


def _cipher(key_path):
    if key_path is None:
        raise SecretsUnavailable('This server has no secret key. Run it as an instance to keep secrets.')
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise SecretsUnavailable('Install the cryptography package on the server to keep secrets.') from None
    path = Path(key_path)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(secrets.token_bytes(32))
            stream.flush()
            os.fsync(stream.fileno())
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
        key = stream.read(33)
    if len(key) != 32:
        raise SecretsUnavailable(f'{path.name} must hold exactly 32 bytes.')
    return AESGCM(key)


def seal(key_path, value, purpose):
    """Encrypt bytes; purpose (such as "two_step:42") binds the result to where it is stored."""
    nonce = secrets.token_bytes(12)
    return VERSION + nonce + _cipher(key_path).encrypt(nonce, bytes(value), purpose.encode())


def open(key_path, sealed, purpose):
    """Decrypt what seal returned for the same purpose. A wrong key or purpose raises SecretsUnavailable."""
    cipher = _cipher(key_path)
    from cryptography.exceptions import InvalidTag
    sealed = bytes(sealed)
    if sealed[:1] != VERSION or len(sealed) < 29:
        raise SecretsUnavailable('A stored secret is damaged.')
    try:
        return cipher.decrypt(sealed[1:13], sealed[13:], purpose.encode())
    except InvalidTag:
        raise SecretsUnavailable("A stored secret does not match this server's secret key.") from None
