# Request integrity

Evlet checks protected inputs and permissions on the server. An unchanged `name`,
`title` or State value may be echoed only when the caller can read it. Submitting
an unreadable value is refused regardless of whether it matches the stored value;
draft previews follow the same rule. Trusted Python handlers retain their existing
authority to update server-owned values.

`Context.list`, `Context.count` and measures use their current read transaction to
check equality and `in` filters on a row-scoped Link. Every supplied linked id must
be readable. Sorting and inequality filters on that scoped relationship remain
unavailable. The HTTP query and Context contracts agree.

## Retrying a create

Clients may send `Idempotency-Key` on `POST /api/{model}`. Use a distinct key for
each intended record, with 1–128 letters, digits, dots, underscores, colons or
hyphens. A UUID is suitable. Keep the key and the same JSON body when retrying
after an interrupted response. Requests without the header keep their existing
behavior. Actions, moves, updates and service endpoints do not gain retry handling.

A successful create and its receipt commit together in the business database.
Within 24 hours, the same key, account, credential and model identify the same
record. Different values with that key return 409. Validation failures do not
reserve the key. Concurrent requests with the same key cannot create two records.
Instances have separate databases and therefore separate key namespaces.

A replay returns 201 with `Idempotency-Replayed: true`, the record's Location and
current ETag. Its body is the **current readable record**, including current
children and action availability, rather than a cached original response. It does
not rerun creation hooks, defaults or effects, and does not repeat transient
`_row_ids` mappings. Current model and row permissions are checked again, fields
are filtered again, and a deleted or now-inaccessible record is not recreated.
After 24 hours the key may create a new record; callers must not depend on a longer
retry window. Expired receipts are removed on keyed creates. The receipt stores a
key hash, request hash and record reference, not request or response contents.

## Login and revocation

Password verification and session admission use the sessions database, so an
ordinary sign-in can finish while a business writer holds the main database.
The account, role, realm and credential generation are rechecked after password
hashing. A revocation racing with admission leaves any stale session unusable.

Account-wide revocation advances a generation in the main database. A cookie
session or pending two-step ticket must match it. Recovering older sessions-file
contents after a committed password reset therefore cannot restore those sessions
or tickets. A password-changing device is retained only by explicitly moving its
session to the new generation. Existing databases gain the generation columns
additively at startup; existing sessions begin at generation zero.

The two-step completion transaction still owns the main write lock: TOTP replay
counters and backup-code consumption must remain atomic. Login independence does
not move those security writes into the sessions file.

## Large releases and discovery

Release restore uses a complete server-owned snapshot, including nested rows.
It can restore collections larger than the 100 rows embedded by a record detail,
while retaining field, scope, row-count and state checks. A normal client sending
a full list for a paginated collection is still refused; clients use child change
sets to preserve rows they have not loaded. Recreated descendant rows and the
restored document commit together or roll back together.

Discovery follows readable children to every declared depth. Read and write JSON
schemas include nested collections, section headings and bounded change sets;
hidden descendants and fields remain omitted. The existing array `items` contract
is retained while a declared collection with change sets also accepts an object.
