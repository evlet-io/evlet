"""Record ids: 63-bit, time-ordered, shown as 13 lowercase Crockford base32 characters.

An id is 42 bits of milliseconds since 01-01-2026 UTC and 21 random bits. Python keeps ids as
integers (`RecordId`); the API, URLs, schemas and tools only ever see the text form, such as
`01hc1sd9vc1ye`. See docs/internal/record-identity.md.
"""

from contextlib import contextmanager
import re
import secrets
import threading
import time

ALPHABET = '0123456789abcdefghjkmnpqrstvwxyz'
LENGTH = 13
EPOCH_MS = 1767225600000  # 2026-01-01T00:00:00Z
RANDOM_BITS = 21
MAX_ID = 2**63 - 1
# Text ids for JSON Schema: 13 Crockford characters, any case, I L O read as 1 1 0.
PATTERN = '^[0-7IiLlOo][0-9A-HJKMNP-TV-Za-hjkmnp-tv-zIiLlOo]{12}$'
SCHEMA = {'type': 'string', 'pattern': PATTERN}
_VALUES = {character: index for index, character in enumerate(ALPHABET)}
_VALUES.update({'i': 1, 'l': 1, 'o': 0})
_TEXT = re.compile('[0-9a-z]{13}', re.ASCII | re.IGNORECASE)
_lock = threading.Lock()
_last = 0
_sequential = False


def encode(value):
    """The 13-character text form of a positive id."""
    if type(value) is bool or not isinstance(value, int) or not 1 <= value <= MAX_ID:
        raise ValueError('Use a positive 63-bit id')
    characters = []
    for _ in range(LENGTH):
        value, digit = divmod(value, 32)
        characters.append(ALPHABET[digit])
    return ''.join(reversed(characters))


def decode(text):
    """The integer of a text id; any case, with I and L read as 1 and O as 0."""
    if not isinstance(text, str) or not _TEXT.fullmatch(text):
        raise ValueError('Use a 13-character record id')
    value = 0
    for character in text.lower():
        digit = _VALUES.get(character)
        if digit is None:
            raise ValueError('Use a 13-character record id')
        value = value * 32 + digit
    if not 1 <= value <= MAX_ID:
        raise ValueError('Use a 13-character record id')
    return value


class RecordId(int):
    """A record id: an int for SQL and Python, the text form for str(), JSON, URLs and tools."""
    __slots__ = ()

    def __new__(cls, value):
        if isinstance(value, RecordId):
            return value
        return super().__new__(cls, parse(value))

    def __str__(self):
        return encode(int(self))

    def __repr__(self):
        return f'RecordId({encode(int(self))!r})'

    def __format__(self, spec):
        # f'{record.id}' is the text form; a numeric spec such as :d still formats the integer.
        return str(self) if not spec else int.__format__(self, spec)

    def __reduce__(self):
        return (RecordId, (int(self),))


def parse(value):
    """An int from a record id given as an int (trusted Python, older clients) or as text."""
    if isinstance(value, str):
        return decode(value)
    if type(value) is bool or not isinstance(value, int) or not 1 <= value <= MAX_ID:
        raise ValueError('Use a record id')
    return int(value)


def record_id(value):
    """A RecordId from an int or text id; None stays None."""
    return None if value is None else RecordId(value)


def text(value):
    """The text form of an id given as an int or text; None stays None."""
    return None if value is None else encode(parse(value))


def new_id():
    """A new id: milliseconds since 2026 in the top 42 bits, then 21 random bits. Strictly
    increasing within one process, even within one millisecond."""
    global _last
    millis = max(0, int(time.time() * 1000) - EPOCH_MS)
    candidate = ((millis & (2**42 - 1)) << RANDOM_BITS) | secrets.randbits(RANDOM_BITS)
    with _lock:
        if candidate <= _last:
            candidate = _last + 1
        _last = candidate
    return RecordId(candidate or 1)


def jsonable(value):
    """A copy of a JSON payload with every RecordId as its text form; other values are unchanged."""
    if isinstance(value, RecordId):
        return str(value)
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def path_id(segment):
    """The RecordId in a URL path segment, or None when the segment is not a text id."""
    try:
        return RecordId(decode(segment))
    except ValueError:
        return None


def from_argument(value):
    """A RecordId from a command line argument: the text id, or a decimal id as older tools printed it."""
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) < LENGTH:
        return RecordId(int(value))
    return RecordId(value)


@contextmanager
def sequential_ids():
    """Number new rows 1, 2, 3 ... per table, as the autoincrement ids before time-ordered ids did,
    for recorded fixtures that must not change between runs. Process-wide; tests and fixture
    scripts only."""
    global _sequential
    previous, _sequential = _sequential, True
    try:
        yield
    finally:
        _sequential = previous


def sequential():
    return _sequential


def checked(value):
    """A RecordId from an int or text id, or None when the value is not a record id."""
    try:
        return RecordId(value)
    except ValueError:
        return None
