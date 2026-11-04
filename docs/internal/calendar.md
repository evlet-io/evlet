# Calendar engine: design

Status: decided by the owner on 04-11-2026 (section 16). Nothing here is built yet. The owner approved
the mockup on 04-11-2026; this spec does not depend on opening it, and section 0 lists its screens.

Evlet gets one calendar engine that any app can use. It knows nothing about any app. It reads
dates from records an app already has, keeps plain events of its own, and works the same on a
desk and on a phone.

## 0. Goals and what already exists

**Goals.** Several calendars, each with its own working schedule. Events that can be passed to
another calendar. `.ics` files everywhere, and email invites when a mailbox is connected.
Scheduling that is decent by default and open to change by apps. No dependency on any app.

**The mockup's five screens**, which the UI section (11) builds:

1. Desktop week view: a sidebar with a mini month and calendar groups (My calendars, Resources,
   From records), a time grid with working hours shading, a now line, all-day items, tentative
   events and a double-booking warning, and a detail panel with the linked record, guests and
   their replies, Hand off, Find a time, Download .ics, Resend invites, reminder and history.
2. Phone agenda: a day strip, calendar chips, the now line, a floating New button and bottom tabs
   (Agenda, Day, Week, Month).
3. Phone hand-off sheet: Move, Share or Delegate, targets with free or busy at that time, and
   "tell guests about the change".
4. Find a time: rules (length, date range, working hours, a gap, a free room), one row per person
   and room with their role, an "everyone free" row, ranked suggestions and a booking link.
5. Calendar settings: weekly schedule (with a short Friday), booking rules, a no-overlap switch,
   who sees what, hand-off rules, email invites, a private feed, a public booking link and
   calendars from records.

**Built on what exists.** The engine adds no parallel mechanism where one exists:

| Need | Existing mechanism | Where |
|---|---|---|
| Writes, validation, locked states | The document write pipeline (`documents.operate`, `Session.update`) | states-actions.md |
| Closed periods | `Meta.period`, `evlet_period_locks`, enforced in the storage layer | PR #100, `docs/internal/api-surface-period-lock.md` |
| Read-only and hidden models | `Meta.api` (`"full"`, `"read"`, `None`) | PR #100 |
| Row and field rules | `Rule(operations, read_fields, write_fields, scope)` and scope forms (`any_of`, `Hierarchy`, `own_customer`) | business-capabilities.md |
| Links to any record | `Reference()` field | model-engine.md |
| History | `Meta.journal = Journal(...)`, Activity tab | journal.md |
| Background work | `JobQueue`, `Task(..., external=True)` for network calls outside a transaction, `CalendarSchedule` | jobs-realtime.md |
| Live updates | `EventStore` record invalidations over SSE | jobs-realtime.md |
| Sending mail | `connectors.send_mail`, `mailboxes.send_mail(user, ..., record=...)` | controls.md (Email, Personal mailboxes) |
| Reading replies | The personal mailbox sync job (`evlet_mail_sync`) | controls.md |
| Outbound address checks | `connectors.safe_address(host, port)` | controls.md (Connectors) |
| Public pages and limits | Realms, public hosts, `@api(public=True)` limit of 30 a minute and 300 an hour per address | routing.md |
| Hashed keys | API keys stored as SHA-256, shown once | controls.md (API keys) |
| AI tools | The Tool Engine, `@api(tool=True)` | tools.md, eva.md |
| Per-person settings | `/settings` preferences, `notify.<event>` | settings.md |
| Date and time pickers | `pickers.ts` (Calendar, DateTimePanel, DurationPanel) | ui-framework.md |

**Two gaps the engine fills generically.** Evlet has no field that holds a user, and mail sending
has no way to attach a file or a `text/calendar` part. Both are small, framework-wide additions
(sections 3 and 9), useful well beyond calendars.

**Name clash.** `evlet/services.py` has an internal `Calendar` class for cron expressions. It is
not exported. Phase 1 renames it `CronCalendar` so `evlet.Calendar` can be the public declaration.

## 1. Records are events

Any model with a date can show on a calendar. The app declares where the dates are. Nothing is
copied: the calendar reads the records, and a drag writes the record.

```python
from evlet import Calendar, Date, DateTime, Duration, Link, Model, State, Text

class DeliveryNote(Model):
    customer = Link("crm.Customer", required=True)
    vehicle = Link("fleet.Vehicle", index=True)
    deliver_at = DateTime(index=True)
    deliver_until = DateTime()
    status = State(("draft", "scheduled", "delivered", "void"), default="draft",
                   locked=("delivered", "void"), moves=(...))

    class Meta:
        period = "deliver_at"
        calendar = Calendar(
            start="deliver_at",
            end="deliver_until",          # None: start plus `length`
            length="PT1H",                # used when end is None or empty
            all_day=False,                # True, False, or the name of a Boolean field
            title="customer.name",        # a field or Link path; default Meta.title_field
            color_by="status",            # a State, Select or Link field
            resource="vehicle",           # a Link: one row per vehicle in the timeline view
            card=("customer", "status"),  # extra fields on the event card
            where={"status__in": ["scheduled", "delivered"]},
            drag=True,                    # False: read only on the calendar
            label="Deliveries",
        )
```

Rules, checked at registration (the app fails to start, naming the model and option):

- `start` is a stored `Date` or `DateTime` with `index=True`. An unindexed start would make every
  week view a full scan.
- `end` is a stored field of the same type, or `None`. With `None`, `length` (a `Duration` value
  or the name of a `Duration` field) gives the end. A `Date` start is always all day.
- `title`, `color_by`, `card` and `resource` name readable fields or Link paths, as list columns do.
- `where` is the same bounded typed filter object a list uses.
- A model may declare a tuple of `Calendar(...)`, each with a `name`, for two date pairs on one
  model (for example "Planned" and "Actual").
- `Meta.calendar` on an `api=None` model is refused: such a model has no caller surface.

**What it changes, surface by surface.**

| Surface | Change |
|---|---|
| Schema (`/api/schema`) | A `calendar` key per model, with only the options whose fields the role can read. A role that can't read `start` gets no `calendar` key, so the model is not a calendar for it. `drag` is false unless the role can write `start` (and `end` when declared) and the model's `api` is `"full"`. |
| List views | The list page gains a Calendar view beside Table and Board (`/app/<model>?view=calendar&date=04-11-2026&span=week`). It uses the list's filters, search and saved views. |
| Routes | `GET /api/_calendar/items?from=&to=&sources=` reads all visible sources in one call (section 11). A record source is an ordinary list query: `start < to AND end >= from`, plus the role's scope and the list filters, capped at 2,000 items per call. |
| Record page | The record's `Download .ics` entry in More (`GET /api/<model>/<id>/calendar.ics`). |
| Desk route | `/calendar` shows every calendar source the person can read (section 11). |

**Dragging goes through the normal write pipeline.** A drag or resize sends
`PATCH /api/<model>/<id>` with the new `start` and `end` and the record's `_revision`. Everything
that applies to any edit applies here:

- write fields and row scopes (a drag that moves a row out of the person's scope rolls back);
- validation and `validate()`;
- locked states (`This record is delivered, so it is locked`), except `editable_when_locked` fields;
- the period lock: a drag from or into a closed period gets
  `This date is in a closed period (locked up to DD-MM-YYYY)`;
- `Meta.api = "read"`: no drag at all;
- journal entries, effects, events and live updates.

The card snaps back and the error shows in a toast. The items route returns `locked_until` per
source with `Meta.period`, so the grid can shade closed days and refuse a drag before sending it.
The server still decides.

Dropping a record card onto another resource in the sidebar or timeline changes the `resource`
field in the same PATCH. That is the only hand-off a record source has; it is a Move (section 5).

## 2. The Event model

Plain events that belong to no record live in a framework app, `evlet.calendar`, shipped inside
the framework and enabled per project like any app. Its models are ordinary Evlet models, so they
get list and record screens, Controls overrides, instance fields, journals, permissions and MCP
tools with no special code.

```python
# evlet/calendar/models.py (sketch)
from evlet import (Boolean, Child, DateTime, Email, Integer, Journal, Link, LongText, Model,
                   Reference, Select, State, Table, Text, User)

class Event(Model):
    calendar = Link("calendar.EventCalendar", required=True, index=True)
    title = Text(required=True, max_length=200)
    starts = DateTime(required=True, index=True)
    ends = DateTime(required=True, index=True)
    all_day = Boolean(default=False)
    zone = Text(default="", max_length=64)          # IANA name; empty: the calendar's zone
    status = State(("tentative", "confirmed", "cancelled"), default="confirmed",
                   moves=(...), final=("cancelled",))
    show_as = Select(("busy", "free"), default="busy")
    visibility = Select(("default", "public", "private"), default="default")
    location = Text(default="", max_length=200)
    online = Text(default="", max_length=500)       # link text people paste; nothing is generated
    notes = LongText(default="")
    record = Reference(index=True)                  # optional: the record this event is about
    organizer = User()
    repeat = Text(default="", max_length=500)       # RRULE subset, section 7
    repeat_end = DateTime(index=True, read_only=True)  # last occurrence end; empty: no end
    series = Link("calendar.Event", index=True)     # an override's or split's original series
    original_start = DateTime(read_only=True)       # RECURRENCE-ID of an override
    uid = Text(read_only=True, max_length=255)      # iCalendar UID, unique per instance
    sequence = Integer(default=0, read_only=True)   # iTIP SEQUENCE

    class Guest(Child):
        user = User()                               # an internal person, or
        email = Email()                             # an external address
        name = Text(default="", max_length=120)
        role = Select(("required", "optional", "info"), default="required")
        reply = Select(("waiting", "accepted", "tentative", "declined"), default="waiting")
        replied_at = DateTime(read_only=True)
        reply_via = Select(("", "link", "email", "desk"), default="", read_only=True)

    class Reminder(Child):
        before = Duration(default="PT15M")          # 0 to 28 days
        channel = Select(("app", "email"), default="app")

    class Skip(Child):                              # EXDATE: one occurrence removed
        original_start = DateTime(required=True)

    guests = Table(Guest)
    reminders = Table(Reminder)
    skips = Table(Skip)

    class Meta:
        title_field = "title"
        open_in = "panel"
        journal = Journal()
        calendar = Calendar(start="starts", end="ends", all_day="all_day", color_by="calendar")
```

Notes:

- `Event` is itself a calendar source through `Meta.calendar`, so plain events and record events
  use one read path, one UI and one drag rule.
- A guest has a `user` or an `email`, not both. An internal guest's address is never stored on the
  event; it is read from the account when mail is sent.
- `uid` is set on create (`<random>@<instance domain>`) and never changes. `sequence` goes up on
  every change that matters to guests: time, repeat, location or cancellation.
- `record` lets any event point at any record (a customer visit at a Customer, a review at a
  Project). The detail panel shows the record's title and a link only when the viewer can read it.
- `visibility = "private"` shows the event as Busy to everyone below the Edit level, even with
  read access to the calendar.
- `User()` is new: an Integer user id, checked against active users on write, shown with the
  person's name and avatar, filterable with `me`. It is generic (an assignee, an owner) and goes
  in phase 2.

## 3. Calendars as records

```python
class EventCalendar(Model):
    name = Text(required=True, max_length=120)
    kind = Select(("person", "team", "resource", "records", "feed"), required=True)
    color = Select(COLORS, default="blue")       # the design tokens' accent set
    owner = User()                               # person calendars: the person
    zone = Text(default="", max_length=64)       # empty: the instance time zone
    # Booking rules
    length = Duration(default="PT30M")
    gap = Duration(default="PT0M")               # free time kept before and after each event
    notice = Duration(default="PT4H")            # minimum notice for a booking
    ahead = Duration(default="P60D")             # how far ahead bookings may be made
    per_day = Integer(default=0)                 # 0: no limit
    step = Duration(default="PT15M")             # where slots may start: :00, :15, :30 ...
    no_overlap = Boolean(default=False)
    handoffs = Select(("ask", "accept", "off"))  # default: ask for person calendars, accept for team and resource
    # Record calendars
    source = Text(default="", max_length=120)    # "<model>" or "<model>:<calendar name>"
    source_filter = Text(default="")             # JSON filter object, checked like a list filter
    resource = Reference()                       # a resource calendar's record (a Vehicle, a Room)
    # External feeds
    feed_url = Text(default="", max_length=2000)
    feed_status = Text(default="", read_only=True)

    class Member(Child):
        user = User()
        role = Text(default="", max_length=64)   # or every person with this role
        level = Select(("book", "free_busy", "read", "edit", "handoff"), required=True)

    class Hours(Child):                          # the weekly schedule
        weekday = Select(("mon", "tue", "wed", "thu", "fri", "sat", "sun"), required=True)
        start = Time(required=True)
        end = Time(required=True)

    class Day(Child):                            # exceptions and holidays
        date = Date(required=True)
        start = Time()                           # both empty: closed all day
        end = Time()
        note = Text(default="", max_length=120)

    members = Table(Member)
    hours = Table(Hours)
    days = Table(Day)
```

**Kinds.**

| Kind | What it holds | Example from the mockup |
|---|---|---|
| `person` | One person's events; `owner` is that person | A sales person's calendar |
| `team` | A shared calendar with members | Sales team, Support rota |
| `resource` | A thing that can be booked; often `no_overlap`; `resource` may point at its record | Meeting room A, Van 2 |
| `records` | A record source (`source`, `source_filter`) shown as a calendar with its own colour and sharing | Deliveries from a Delivery Note date |
| `feed` | Read-only items from an external `.ics` URL (section 8) | UAE holidays |

Every person gets a person calendar on first use of `/calendar`, created by trusted code.

**Working schedule.** `hours` holds any number of ranges per weekday, so a split day or a short
Friday (`fri 08:00 to 12:00`) is two or one rows. `days` overrides a date: a holiday (closed) or
different hours. A `feed` calendar can be named as a holiday source in settings: its all-day items
close those days for the calendars that use it. Times are wall clock in the calendar's zone. The
zone defaults to the instance time zone (`Instance(timezone=...)`). The project starter writes
`timezone="Asia/Dubai"` (GST) into new instance configs; the calendar never hard-codes a zone.

**Booking rules** apply to booking links and to Find a time suggestions. Desk edits are not
refused by them; the UI warns ("Outside working hours", "Third booking today, limit 2"). Only
`no_overlap` refuses a desk edit.

**No overlap, enforced in the save transaction.** When a calendar has `no_overlap`, a write that
makes two busy items overlap on it is refused with
`{"starts": "Van 2 is already booked from 10:00 to 11:00"}` (422). The check:

- runs inside the write transaction of the create, update, move, hand-off or booking, after the
  new values are known and before commit, for both plain events and record sources;
- counts events with `show_as = "busy"` and a status other than `cancelled`, the occurrences of
  repeating events in the range, and the calendar's record source rows;
- adds `gap` on both sides;
- applies to the event's own calendar and to every `no_overlap` calendar it is shared with
  (section 5), so a room booked through Share is protected too.

The query is one indexed range read per calendar:

```sql
SELECT id FROM calendar_event
 WHERE calendar_id = ? AND starts < ? AND ends > ? AND id <> ?
   AND show_as = 'busy' AND status <> 'cancelled'
 LIMIT 1
```

plus the repeating series whose range touches the window, expanded in Python (at most one
window, so this is bounded).

**Why two bookings can't race.** A check made before the write, for example in the browser or in
a separate request, can be beaten by a second booking in between. So the check is never made
there. It is made inside the write transaction, and Evlet already serializes writers per database:
SQLite write transactions start with `BEGIN IMMEDIATE`, and PostgreSQL write transactions take the
database's transaction advisory lock before any read (`docs/postgresql.md`). The second booking
waits for the first to commit, then reads it and is refused.

To keep this true if PostgreSQL writes stop being fully serialized one day, the check calls a new
`tx.serialize(key)` first. On SQLite, and on PostgreSQL while the database lock is held, it does
nothing. Otherwise it takes `pg_advisory_xact_lock` on a hash of `calendar:<id>`. Phase 2 adds a
test that starts two bookings of the same slot at once from two threads (and two processes on
SQLite) and asserts exactly one succeeds, on both engines.

## 4. Sharing levels

A calendar member has one level. Levels are on top of the role's `Rule` for `Event`: a person
needs both. A level never grants what the role does not have, and a role never reaches a
calendar the person is not a member of (the calendar's owner and admins are members implicitly).

| Level | Sees | Can do |
|---|---|---|
| `book` | Nothing in the calendar; only open slots on its booking links | Book through a booking link |
| `free_busy` | Busy blocks: start, end and Busy. Never a title, guest, location or field | Find a time, invite the calendar |
| `read` | Event details, subject to the role's field rules | Download .ics |
| `edit` | Same as read | Create, change, cancel events on the calendar |
| `handoff` | Same as read | Edit, plus Move, Share and Delegate out of the calendar |

`read` is a fifth level beside the four the owner first listed, decided on 04-11-2026, so a person
can see a team's meetings without being allowed to edit them.

**How it is enforced.** A new row scope form, `calendar_member(level)`, reads the member table
like `own_customer` reads user links. The framework's default rules for `Event` are:

```python
permissions = {
    ("editor", Event): Rule(("read", "create", "update", "delete"),
                            scope=calendar_member("read"), write_scope=calendar_member("edit")),
    ("viewer", Event): Rule(("read",), scope=calendar_member("read")),
}
```

`write_scope` is the one addition: a second scope that creates, updates and deletes must also
pass. Apps and Controls can narrow these rules as for any model.

**Free and busy reads never leak.** `GET /api/_calendar/busy?calendars=&from=&to=` is a separate
route that runs as trusted code and returns only:

```json
{"calendars": {"14": [["2026-11-04T06:00:00Z", "2026-11-04T07:30:00Z"]]}, "zone": "Asia/Dubai"}
```

- It returns intervals, never ids, titles, guests, colours or counts. Overlapping and touching
  intervals are merged first, so the number and edges of single events can't be read off.
- It answers only for calendars where the person has at least `free_busy`, and a calendar they
  can't reach gets the same 404 as one that does not exist.
- For a `records` calendar it reads the source as trusted code with the calendar's own
  `source_filter`, so a person with `free_busy` sees a van is busy without reading delivery notes.
  Only `start` and `end` leave the query; the role's field rules never apply because no field
  value is returned.
- A `private` event is Busy below `edit`, on every route, including the items route and `.ics`.

**Record calendars** keep the model's own rules. A member with `read` on a `records` calendar sees
details only when their role can read the model and the row; otherwise they see Busy. Edit on the
calendar never grants a write the model's rules refuse.

## 5. Hand-off

Hand-off passes an event to another calendar. It is a declared action on `Event`, so it has a
dialog, a journal entry and an HTTP route like every action:
`POST /api/calendar.event/<id>/actions/hand_off`.

```python
@action(inputs={"mode": Select(("move", "share", "delegate")), "to": Link("calendar.EventCalendar"),
                "keep_me": Boolean(default=True), "tell_guests": Boolean(default=True),
                "note": Text(default="", max_length=500)})
def hand_off(self, context, mode, to, keep_me, tell_guests, note): ...
```

| Mode | What changes | Typical use |
|---|---|---|
| Move | `calendar` becomes the target. The organizer stays. It leaves the old calendar. | A job moves from Van 2 to Van 3; a meeting moves to the Support rota |
| Share | The event stays, and also shows on the target (`Event.Shared` rows). It is busy on both. | Add Meeting room A to a meeting |
| Delegate | The target calendar's owner becomes the organizer, and the event moves to their calendar. With `keep_me`, the old organizer stays as an optional guest. | A manager hands a customer call to a colleague |

Rules:

- The person needs `handoff` on the source calendar. On the target they need `edit`, or the
  target's `handoffs` must be `ask` or `accept`. Delegate needs a `person` or `team` target.
- **Acceptance.** With `handoffs = "ask"`, the change is a request: a `HandOff` row (`event`,
  `mode`, `from_calendar`, `to_calendar`, `by`, `note`, `state`: requested, accepted, declined,
  withdrawn, expired). The event shows on the target as tentative with "Waiting for you". Accept
  applies the change in one transaction; Decline leaves the event as it was. A request expires at
  the event's start. With `accept`, the change applies at once when the sender has `edit` on the
  target, and is a request otherwise. With `off`, nothing can be handed to it. New person calendars
  start with `ask`; new team and resource calendars start with `accept`.
- **Free-time check.** The sheet lists targets with Free or Busy at that time, from the busy route
  (so a `free_busy` member sees no more than they may). When the target is `no_overlap` and busy,
  the action is refused in the transaction (section 3). Otherwise a busy target is a warning.
- **History.** Every hand-off and every answer writes a journal entry on the event: who, mode, from,
  to, note, accepted by. `HandOff` rows are the list the detail panel shows under History.
- **Guests.** With `tell_guests`, external guests get an updated invite (REQUEST with a higher
  SEQUENCE, new ORGANIZER for Delegate), and internal guests get an app notification. Move to a
  calendar nobody outside sees changes nothing for guests and sends nothing.
- A repeating event hands off the whole series, one occurrence ("this one") or this and following
  (section 7), using the same split rules.
- A record source has Move only, by changing its `resource` field (section 1).

## 6. Find a time

**Inputs.** People (internal users), calendars (team, resource, records, feed), each with a role
(`required`, `optional` or `guest`), an optional set of rooms of which any one will do, a length,
a date range, whether to keep to working hours, a gap, and how many suggestions to return.
External guests have no free time Evlet can see; they are listed as "unknown" and never block.

**Algorithm.** All times are UTC integer seconds inside; zones apply only when building working
hours and when showing results.

1. **Busy.** For each calendar, read its busy intervals in the window: events (repeats expanded),
   shares, record source rows and feed items, through the same trusted read as the busy route.
   Pad each by the larger of the request's gap and the calendar's `gap`. Sort and merge.
2. **Open.** Build each calendar's open intervals: the window, cut to its weekly hours in its zone
   (with `days` exceptions and holiday feeds), minus busy. With working hours off, the window minus
   busy. A person's open time is the union over their own calendars' hours and the intersection
   over their busy.
3. **Required.** Intersect the open lists of all required participants: one linear merge per list.
4. **Rooms.** For each room, intersect its open list with the result of step 3, tagged with the
   room. A slot needs one room to be open for the whole slot, so rooms are not merged together.
5. **Candidates.** Slide a slot of the given length through each interval, starting on `step`
   boundaries in the organizer's zone. Drop slots before minimum notice or past the maximum ahead,
   and days already at `per_day` on any calendar with a limit.
6. **Rank.** Score each candidate and keep the best `limit` with a bounded heap:
   - plus 100 for each optional participant who is free;
   - minus 2 for each day after the first day of the range;
   - plus 10 when the slot touches the start or end of a free block (it leaves fewer small gaps);
   - plus 5 when the slot is inside every required person's core hours (the middle of their day,
     excluding the first and last hour);
   - minus 1 for each room taken over the smallest suitable one, in the order rooms were given;
   - ties break on earlier start, then room order. The result is deterministic.
7. **Answer.** Each suggestion lists its start, end, room, and every participant's status
   (free, busy, unknown), with busy shown as Busy only.

Each step is a linear pass over sorted intervals, so one search is O(n log n) in the number of busy
intervals read.

**Permissions.** The person needs at least `free_busy` on every calendar listed. A person or room
they can't see gets the 404 of a calendar that does not exist. The result never names an event.

**Limits.** A range of at most 31 days (14 by default), at most 30 people and calendars, at most 20
rooms, at most 20,000 busy intervals read, a step of at least 5 minutes, at most 50 suggestions.
Over a limit is a 422 naming the input.

**Performance targets**, measured by a new `scripts/benchmark_calendar.py` and recorded in
`docs/performance.md`: 10 people and 5 rooms over 14 days, each with 40 events a week including
repeats, answers in 150 ms or less at the 95th percentile on SQLite on the reference laptop, and
400 ms or less at the limits. PostgreSQL is measured in CI and reported, not gated.

**Python API.** A public function any app or trusted code can call, and a framework route that
runs it as the person:

```python
from datetime import date, timedelta
from evlet.calendar import Attendee, find_time

slots = find_time(
    context,
    attendees=(Attendee.user(7), Attendee.user(9, role="optional"), Attendee.calendar(14)),
    rooms=(Attendee.calendar(21), Attendee.calendar(22)),     # any one of these
    length=timedelta(minutes=45),
    between=(date(2026, 11, 5), date(2026, 11, 18)),
    working_hours=True,
    gap=timedelta(minutes=10),
    limit=10,
)
slots[0].start, slots[0].end, slots[0].room, slots[0].status   # status: {attendee: "free"|"busy"|"unknown"}
```

`POST /api/_calendar/find-time` takes the same inputs as JSON (dates as `YYYY-MM-DD`, lengths as
ISO durations) and returns the suggestions and one row of merged busy intervals per attendee, which
the Find a time screen draws. Apps that want their own endpoint wrap it:

```python
@api(method="POST", permission=(Event, "read"), tool=True)
def find_a_time(context, people: str, length: int, days: int = 14):
    """Suggest meeting times when the given people (comma separated user ids) are all free."""
```

## 7. Repeating events

`Event.repeat` holds an RRULE in RFC 5545 syntax, limited to this subset:

| Part | Values |
|---|---|
| `FREQ` | `DAILY`, `WEEKLY`, `MONTHLY`, `YEARLY` |
| `INTERVAL` | 1 to 999 |
| `COUNT` | 1 to 1,000 (not with `UNTIL`) |
| `UNTIL` | A date, or a UTC date-time |
| `BYDAY` | `MO` to `SU`; with MONTHLY or YEARLY an ordinal, such as `1MO` or `-1FR` |
| `BYMONTHDAY` | 1 to 31 and -1 to -31, with MONTHLY |
| `WKST` | `MO` to `SU`, default `MO` |

Anything else is refused on write with a plain message ("Repeats can use daily, weekly, monthly or
yearly rules"). External feeds may carry more; their items are expanded with the same subset and
any rule outside it is shown as its first occurrence only, with a note.

**Expansion** is a pure function in `evlet/calendar/repeat.py`, standard library only (`datetime`,
`zoneinfo`, `calendar`):

- occurrences are computed in the event's zone on the wall clock, so 09:00 stays 09:00 across a
  daylight saving change; a time that does not exist that day moves forward by the gap, and an
  ambiguous one takes the first;
- `BYMONTHDAY=31` skips months without a 31st, as RFC 5545 says;
- skips (`Event.Skip`) remove occurrences; overrides (events with `series` and `original_start`)
  replace them;
- one call expands at most one window of 2 years and 1,000 occurrences per series; the items
  route asks only for the visible range;
- `repeat_end` (the end of the last occurrence, empty when the rule has no end) is stored, so the
  range query finds a series with `starts < to AND (repeat_end IS NULL OR repeat_end >= from)`.

**Changing one occurrence.** Edit, drag, cancel and hand-off on an occurrence ask "This event" or
"This and following events". All events of a series is the third choice when the change is not a
drag.

- *This event*: an override event is created with `series` and `original_start`; cancelling one
  adds a skip instead. Guests get the override (a VEVENT with RECURRENCE-ID).
- *This and following*: the series is split. The original gets `UNTIL` just before the chosen
  occurrence (a `COUNT` is turned into the matching `UNTIL`). A new series starts at the chosen
  occurrence with the changed values, a new `uid`, the same guests, and `series` pointing at the
  original. Overrides and skips after the split move to the new series. Guests get an updated
  REQUEST for the original and a new REQUEST for the new series, which every client handles.
  RANGE=THISANDFUTURE is not used, since many clients ignore it.
- The no-overlap check covers every occurrence of a changed series inside the 2-year window.

## 8. ICS

`evlet/calendar/ics.py` writes and reads iCalendar (RFC 5545) with the standard library: CRLF line
endings, folding at 75 octets, text escaping, and UTF-8.

**Fields written.** `VCALENDAR` with `VERSION:2.0`, `PRODID:-//Evlet//Calendar//EN`,
`CALSCALE:GREGORIAN` and `METHOD` for invites. `VEVENT` with `UID`, `DTSTAMP`, `DTSTART`, `DTEND`
(or `VALUE=DATE` for all-day items, with an exclusive end date), `SUMMARY`, `DESCRIPTION`,
`LOCATION`, `URL` (the event's page), `STATUS`, `TRANSP` (from `show_as`), `CLASS` (from
`visibility`), `SEQUENCE`, `CREATED`, `LAST-MODIFIED`, `ORGANIZER`, `ATTENDEE` with `CN`, `ROLE`,
`PARTSTAT` and `RSVP`, `RRULE`, `EXDATE`, `RECURRENCE-ID`, and `VALARM` (`ACTION:DISPLAY`,
`TRIGGER`). The online link text goes in `DESCRIPTION` and `LOCATION` when there is no place.

**Time zones.** `DTSTAMP`, `CREATED` and `LAST-MODIFIED` are UTC. `DTSTART` and `DTEND` use
`TZID=<IANA name>`, with a `VTIMEZONE` built from `zoneinfo` for the years the file covers. A
zone with no changes, such as Asia/Dubai, gets one `STANDARD` block (`TZOFFSETFROM` and
`TZOFFSETTO` `+0400`). Reading accepts IANA names, the common Windows names (a small table, for
Outlook), a file's own `VTIMEZONE` offsets as a fallback, and UTC.

**Per event.** `GET /api/calendar.event/<id>/calendar.ics` and
`GET /api/<model>/<id>/calendar.ics` for record sources, with read access. Busy-only viewers get a
file with `SUMMARY:Busy` and nothing else.

**Private feed per calendar.** A member with at least `free_busy` can create a feed link for one
calendar from its settings:

- the key is 32 random bytes, shown once as
  `https://<host>/api/_calendar/feed/<key>.ics`; only its SHA-256 is stored, in
  `evlet_calendar_feeds(id, calendar_id, user_id, key_hash, prefix, created_at, last_used_at)`;
- Replace key makes a new one and the old one stops at once; Remove deletes it;
- a feed reads as its person with their current level at each fetch, so a removed member or a
  deactivated account gets 404; details follow their level, so a `free_busy` feed has Busy only;
- it covers 90 days back and 365 ahead, with repeats as `RRULE`;
- it answers with `ETag` and `Cache-Control: private, max-age=300`, at most 60 fetches an hour per
  key (429 over that), and is logged without the key;
- feeds are off unless the `calendar.feeds` setting in Controls is on.

**External feeds.** A `feed` calendar has a `feed_url`. A job (`evlet_calendar_feed`, a
`Task(..., external=True)` so no database transaction is open during the fetch) reads it:

- `https` only; the host is resolved and checked with `connectors.safe_address` (no loopback,
  link-local, metadata, multicast or reserved addresses) and the connection goes to the checked
  address; at most 3 redirects, each checked again; no cookies or credentials are sent;
- 10 seconds in all (`connectors.TIMEOUT`), 2 MB at most, then the fetch stops;
- the parser stops at 5,000 components, 10 levels of nesting and 10,000-character lines;
- items replace the calendar's previous items in one short transaction, in
  `evlet_calendar_feed_items(calendar_id, uid, original_start, starts, ends, all_day, summary,
  rrule, show_as)`; `DESCRIPTION`, attendees and attachments are not kept;
- the feed is refreshed every hour by default and never more often than every 15 minutes; failures
  show in `feed_status` and under Needs attention in Controls after a day of failures;
- feed calendars are read only: no drag, no edit, no hand-off.

## 9. Email invites

Invites follow iTIP (RFC 5546) and go through the mail Evlet already has. No mail provider is
required: without one, everything still works by `.ics` download and reply links.

**Sending.** When an event with external guests is created, changed in a way that matters
(section 2), cancelled, or a guest is added or removed, a job (`evlet_calendar_invite`,
`external=True`, one per event change, keyed so a retry never sends twice) sends:

| Change | Method | To |
|---|---|---|
| Created, changed, guest added, handed off with "tell guests" | `REQUEST` | Every external guest (an added guest alone when nothing else changed) |
| Cancelled or deleted, guest removed | `CANCEL` | The guests concerned |

Mail goes through `business.mailboxes.send_mail(organizer, to, subject, text, html, record=(Event,
id), ...)`, so it leaves from the organizer's own mailbox when they connected one, and from the
default outgoing account otherwise. Replies to it are then linked to the event's Activity like any
other mail. Internal guests get app notifications instead, by their `notify.<event>` preferences,
and email only when they chose it.

`Connectors.deliver` and both `send_mail` functions gain one generic keyword:
`parts=(Part(content_type, data, filename=None, method=None), ...)`. An invite is the plain text,
an HTML body, a `text/calendar; method=REQUEST` alternative and the same file as `invite.ics`.
At most 5 parts and 2 MB in all. This is the one change to the mail code.

**Replies.** Two ways, both matched to one guest:

- *Reply links.* Each invite carries Accept, Maybe and Decline links with a per-guest key
  (shown only in that email, stored as a SHA-256 on the guest row). The link opens a small public
  page, `/calendar/reply/<key>`, that shows the event's title, time and organizer and records the
  answer. It works without any incoming mail. The page runs under the public rate limit.
- *Email replies.* The personal mailbox sync already reads the organizer's inbox. It gains one
  step: a message with a `text/calendar` part whose `METHOD` is `REPLY` is parsed (with the same
  limits as feeds). It is applied only when the `UID` names an event this mailbox's owner
  organizes, the `ATTENDEE` address matches a guest on it, the message's From address is that same
  address, and its `SEQUENCE` is not older than the event's. It changes that guest's `reply` and
  nothing else. Shared IMAP accounts join when reading them arrives (controls.md says this comes
  later).

A forged From can at most change one guest's answer, which the organizer sees in the guest list
with "by email". Reply links are the stronger path and are on by default.

**Resend invites** in the detail panel queues a REQUEST to every external guest whose answer is
waiting. **Reminders** for internal people go through the same notification preferences; email
reminders are sent by a calendar schedule that checks due reminders every minute.

## 10. Booking links

A `BookingLink` lets people outside the desk book time on one calendar or on any of a pool of
calendars, by the same rules as Find a time.

```python
class BookingLink(Model):
    title = Text(required=True, max_length=120)          # "Book a service visit"
    slug = Text(read_only=True, max_length=40)           # random, with an optional readable prefix
    calendars = Table(...)                               # one calendar, or a pool (first free, then least booked)
    lengths = Text(default="PT30M")                      # one or more ISO durations to choose from
    realm = Select(("public", "portal"), default="public")
    confirm_email = Boolean()                            # held as tentative until the guest confirms; default on for public, off for portal
    approve = Boolean(default=False)                     # the calendar owner accepts each booking
    questions = Table(...)                               # name and email always; up to 5 more short fields
    per_email_day = Integer(default=2)
    per_day = Integer(default=0)                         # 0: the calendar's own limit
    active = Boolean(default=True)
```

**Where it is served.** A public host (`auth=None`) or the primary host at `/book/<slug>`, which
becomes a reserved path. A `portal` link is served on portal hosts only and needs a signed-in
portal user; their name and email come from the account, and the event's `record` points at their
linked record (for example their customer, through `evlet_user_links`).

**Routes.** `GET /api/_book/<slug>` (the link's title, lengths, questions and zone),
`GET /api/_book/<slug>/slots?date=&length=` (open start times only, from `find_time` with the link's
calendars, booking rules and working hours; never busy blocks or anything about other events) and
`POST /api/_book/<slug>` (the booking). The booking re-runs the slot check inside the write
transaction with the no-overlap rule always on for the link's calendars, so two people can never
take the same slot (section 3). It creates the `Event` (organizer: the calendar owner, guest: the
booker) and queues the invite. The invite carries a manage link (with its own hashed key) to
cancel or move the booking within the same rules.

**Abuse protection.**

- The built-in public limit (30 calls a minute and 300 an hour per address) applies to every
  booking route and cannot be turned off.
- At most `per_email_day` bookings per email address per link per day, and at most 3 unconfirmed.
- With `confirm_email`, a booking holds the slot as tentative for 30 minutes; the emailed link
  confirms it; unconfirmed holds are cancelled by a schedule and free the slot.
- A hidden field that people leave empty; a booking that fills it is answered as if it worked and
  dropped.
- Minimum notice and maximum ahead come from the calendar.
- No CAPTCHA: it would need a third party. An inactive or removed link answers 404.
- Booker input is plain text, stored with the model's normal limits, and never shown as HTML.

## 11. UI

All of it is in Evlet's own UI framework, in a new `frontend/src/evlet/calendar/` folder loaded as
its own chunk, like Controls. It reads only the routes in this spec and the schema.

**Views.**

| View | What it shows |
|---|---|
| Day, Week | A time grid: working hours shaded by the calendar's schedule, the now line, an all-day row, overlapping events side by side, tentative events hatched, a red warning on double bookings in calendars without `no_overlap` |
| Month | Six weeks, up to three items a day then "+4 more" |
| Agenda | A list by day, endless in both directions, paged by range |
| Timeline | One row per resource (resource calendars, or a record source's `resource` values) across hours or days |

**Routes.** `/calendar` (all calendars), `/calendar/:view/:date` (for example
`/calendar/week/04-11-2026` in the person's date format, which the router reads), `?open=<id>` for
the detail panel, `?cal=14,21` for the calendars shown, `/calendar/find-time` and
`/calendar/settings/:id`. The list page's Calendar view (section 1) uses the same components.

**Data.** `GET /api/_calendar/items?from=&to=&sources=` returns, per source, the items in range
(repeats expanded), each with `id`, `model`, `start`, `end`, `all_day`, `title`, `color`, `card`
values, `drag`, `busy_only` and `tentative`, plus `locked_until` per source and the working hours
per calendar for the range. At most 2,000 items per call; month view asks for 42 days. Live updates
come from the existing SSE invalidations of `Event` and of each source model; only the visible
range is fetched again. The server answers a week of 500 items in 100 ms or less.

**Phone.** Below 640 px:

- Agenda is the default, with a day strip, calendar chips, the now line and a floating New button;
  the bottom tabs switch Agenda, Day, Week (three days) and Month;
- swipe left and right changes the day or range; a long press (500 ms) picks an event up, then
  drag moves it; tap an empty slot to start a new event there;
- the detail panel and the hand-off sheet open as drawers from the bottom (`openDrawer`);
- every target is at least 44 px tall.

**Drag, resize and drop.** Pointer events, snapping to 15 minutes (a setting per person). The card
moves at once and the PATCH follows; an error moves it back with the server's message. A successful
move shows a toast with Undo, which sends the reverse PATCH. Dropping an event onto a calendar in
the sidebar opens the hand-off sheet with Move chosen (Share with Alt or Option held). Dragging is
offered only when the item's `drag` is true.

**Keyboard and screen readers.** The grid is an ARIA grid. Every event is a button labelled
"Site visit, Tuesday 04-11-2026, 10:00 to 11:00, Van 2". Arrows move between slots and events,
Enter opens, N starts a new event at the focused slot, T goes to today, D, W, M, A and L switch
views, Alt with an arrow moves the focused event by one step or one day, Alt and Shift with Up or
Down change its length, Delete cancels it after a confirm, Escape closes the panel. Every drag has
a keyboard way.

**Components** in `frontend/src/evlet/calendar/`, using the existing `ui/` parts (SplitView,
DocFrame panel variant, `openDrawer`, `openPopover`, Menu, Chip, Avatar, `pickers.ts`):

| Module | Components |
|---|---|
| `page.ts` | CalendarPage: the shell, routes and view switch |
| `sidebar.ts` | CalendarSidebar: the mini month (the existing `Calendar` picker), calendar groups with colour dots and check boxes |
| `grid.ts` | TimeGrid (day, week), the all-day row, the overlap layout, the now line |
| `month.ts`, `agenda.ts`, `timeline.ts` | MonthGrid, AgendaList with DayStrip, Timeline |
| `card.ts` | EventCard with title, time and the source's `card` fields |
| `panel.ts` | EventPanel: details, linked record, guests with replies, reminders, history, actions |
| `handoff.ts` | HandOffSheet: mode, targets with Free or Busy, keep me, tell guests |
| `findtime.ts` | FindTime: rules, one row per attendee, the everyone-free row, suggestions, Make a booking link |
| `settings.ts` | WeeklyHours, DayExceptions, BookingRules, Members, Feeds; the rest of the calendar's settings is its generic record page |
| `repeat.ts`, `guests.ts` | RepeatEditor (the RRULE subset in plain words), GuestList |
| `drag.ts` | Pointer and keyboard drag, resize and drop, shared by every view |

The booking and reply pages are a small separate entry with no desk code.

**Customisation hooks.**

- *Per-calendar colours* from `EventCalendar.color`; each person can override a colour for themselves
  (kept with their layout preferences, never with records).
- *Card fields* from `Calendar(card=...)` and, for `Event`, from Controls instance fields marked to
  show on the card.
- *App views.* The browser has no runtime plugin registry today, so apps declare views in Python
  and the calendar page renders them:

  ```python
  from evlet.calendar import CalendarView
  views = (
      CalendarView("dispatch", label="Dispatch board", view="timeline", sources=(Job,),
                   group_by="technician", hours=("07:00", "19:00"), span="day",
                   filters={"status__in": ["scheduled", "on_way"]}),
  )
  ```

  A view needing its own drawing uses an app frontend (`Frontend(kind="static")`) over the same
  `/api/_calendar` routes. A browser plugin registry comes only if an app needs drawing these views
  can't do.

## 12. Eva and MCP tools

Three framework tools in `evlet/tools.py`, listed only when the calendar app is enabled. Each calls
the routes above as the person, so every level, rule and limit applies, and every call is in the
Tool log.

| Tool | Kind | Route | Notes |
|---|---|---|---|
| `calendar_free_time` | Read | `GET /api/_calendar/busy` | Busy intervals and open hours for calendars the person can see, in their zone |
| `find_time` | Read | `POST /api/_calendar/find-time` | The same inputs and limits as the screen |
| `create_event` | Write | `POST /api/calendar.event` | Title, start, end, calendar, guests, location, notes, record. The MCP client asks its user, as for any create. Sending invites to external guests needs approval in Evlet, like app actions |

Changing, cancelling and handing off use the existing `update_record`, `change_state` and
`run_action` tools. Eva gets the same tools. An Eva scheduled task can use them like any other.

## 13. Customisation for apps

What an app can do, without framework changes:

- declare `Meta.calendar = Calendar(...)` on any of its models, with several date pairs;
- add instance fields to `Event`, `EventCalendar` and `BookingLink` through Controls, and show
  them on cards;
- narrow the default `Event` rules, or add its own on its models;
- add booking questions and `CalendarView`s;
- pick record calendars and feeds from Calendar settings;
- add scheduling rules through one hook object, registered in its `AppDefinition`:

```python
from evlet.calendar import Scheduling

scheduling = Scheduling(
    slot_check=check_slot,     # (context, slot) -> None, or a plain reason to drop the slot
    score=adjust_score,        # (context, slot, score) -> new score
    booked=on_booked,          # (context, event) runs in the booking transaction, as trusted code
)
app = AppDefinition(..., scheduling=scheduling)
```

Hooks run as trusted code in Find a time, booking links and the booking transaction, with a time
budget (50 ms per call; a hook over budget is logged and its slot kept).

**Example: field service.** A `Job` model with `scheduled_at`, `scheduled_until` and a
`technician` Link declares `Meta.calendar = Calendar(start="scheduled_at",
end="scheduled_until", resource="technician", color_by="status", card=("customer", "area"))`.
Each technician has a `resource` calendar with `no_overlap`, a 30-minute `gap` for travel and
their own weekly hours. A `CalendarView("dispatch", view="timeline", group_by="technician")` is the
dispatch board; dragging a job to another technician's row changes `technician` through the normal
pipeline, so a job in a locked state can't move. A public booking link "Book a service visit"
offers 2-hour slots on the pool of technicians. A `slot_check` drops slots where the technician
does not cover the customer's area.

**Example: room booking.** Each room is a `resource` calendar with `no_overlap` and
`resource=Reference` to a `Room` record that holds capacity. People book rooms by adding them to a
plain `Event` (Share) or in Find a time ("any one of" Meeting room A, B, C). A `slot_check` drops
rooms whose capacity is below the number of guests. A portal booking link lets tenants of the
building book the shared hall, limited to 2 bookings a day per email. The building's holidays come
from an `.ics` feed calendar.

Neither example needs code in the framework. HR, sales or any other app-specific behaviour stays
in apps.

## 14. Out of scope for now

- Two-way sync with Google or Outlook calendars. Feeds in and out and email invites cover the
  common cases; two-way sync is a connector of its own later.
- Creating video call links. The `online` field holds link text people paste.
- AI scheduling: choosing times or answering invites without a person. Eva can call the tools.
- Free and busy lookups on other servers (CalDAV, iSchedule) and serving CalDAV.
- Travel time from maps, resources with quantities (10 chairs), and waiting lists on booking links.

## 15. Phasing

Each PR merges on its own, keeps every existing test passing, adds tests on SQLite and on
PostgreSQL (CI), updates the public docs (a new `docs/calendar.md`, plus `api-stability.md`,
`performance.md`, `tools.md`, `routing.md` where touched) and shows its feature in the Playground.

**PR 1: Records are events.** `Calendar(...)` and its registration checks; the schema key;
`GET /api/_calendar/items` for record sources; the Calendar view on list pages (Week, Month,
Agenda, with drag and resize); per-record `.ics` download and the ICS writer; the `CronCalendar`
rename. Tests: registration errors; range queries at the edges; scope and field rules; drags refused
by write fields, locked states, `api="read"` and the period lock; ICS output checked line by line
against fixtures; screen tests from recorded fixtures. Playground: a dated Playground model shows
as a calendar, with one closed period.

**PR 2: Calendars, events, sharing and hand-off.** The `evlet.calendar` app with `EventCalendar`,
`Event`, guests, reminders (in-app), schedules and exceptions; `User()`; `calendar_member` and
`write_scope`; the busy route; `no_overlap` with `tx.serialize`; hand-off with acceptance and
history; `/calendar` with the sidebar, day, week, month, agenda and timeline views, the detail
panel, the phone layout and the hand-off sheet; calendar settings. Tests: every level against
every route, including that busy reads carry no titles or fields; the two-thread booking race on
both engines; hand-off modes and answers; keyboard paths. Playground: Sales team, Support rota,
Meeting room A and Van 2.

**PR 3: Repeats, feeds and invites.** The RRULE subset and its expansion; this one and this and
following; the ICS reader; private feeds with replaceable keys; external feeds through the job and
the address checks; `parts=` on mail sending; REQUEST and CANCEL; reply links; REPLY through the
mailbox sync; email reminders. Tests: expansion vectors (month ends, daylight saving zones, COUNT
and UNTIL); split series; feed limits (size, time, redirects to private addresses); invite and
reply round trips against a local SMTP and IMAP test server already used by the mail tests.
Playground: a repeating stand-up, a UAE holidays feed from a bundled file, and invites to the
local test mailbox.

**PR 4: Find a time, booking links and tools.** `find_time` and its route; the Find a time screen;
`BookingLink` with its public and portal pages; the abuse limits; the `Scheduling` hooks; the three
tools; the benchmark. Tests: algorithm vectors (required, optional, rooms, gaps, notice, per-day
limits, ranking ties); permission checks on every attendee; the booking race; rate limits; tool
calls through the Tool Engine. Playground: a public "Book a demo" link on `public.localhost`.

PR 1 needs PR #100 (period lock) merged first. PRs 2 to 4 follow in order.

## 16. Decisions

The owner decided all fifteen points on 04-11-2026, accepting each recommendation as written.

1. **Names.** The declaration is `Calendar(...)` in `Meta.calendar`, the calendar record model is
   `EventCalendar`, and the internal cron helper becomes `CronCalendar`. `CalendarSource(...)` would
   free `Calendar` for the model but reads worse in models.
2. **Where events live.** In an optional framework app, `evlet.calendar`, enabled per project.
   Record calendars (PR 1) work without it, so a project with no use for plain events gets no extra
   tables.
3. **A `User()` field.** Added in PR 2, rather than storing user ids in Integer fields. It is
   generic and also serves owners and assignees in apps.
4. **A fifth level, `read`** (see details, no edit), between free time and edit. Without it, a
   person who should see a team's meetings would have to be allowed to edit them.
5. **Default time zone.** Calendars use the instance zone, and the framework's own default stays
   UTC. The project starter writes `timezone="Asia/Dubai"` (GST) into new instance configs as the
   suggested value. The calendar never hard-codes a zone.
6. **Booking rules on desk edits.** The desk warns; booking links refuse. Only `no_overlap`
   refuses a desk edit.
7. **Reply links.** On by default, beside email replies, so invites work with outgoing mail only.
8. **Hand-off acceptance default.** `ask` for person calendars; `accept` for team and resource
   calendars when the sender has `edit` on the target.
9. **App views.** Python `CalendarView` declarations now. A browser plugin registry comes only if
   an app needs drawing these views can't do.
10. **Booking confirmation by email.** On for public links, with a 30-minute tentative hold; off
    for portal links, where the person is signed in.
11. **Private feed contents.** Off unless enabled in Controls. When on, each person's feed follows
    their own level, so busy-only members get Busy only.
12. **External feed refresh.** Every hour, never more often than every 15 minutes.
13. **Notifications.** Sending notifications is not built yet (settings.md). PR 2 shows reminders
    and hand-off requests in the app while it is open, through the existing live updates. PR 3
    sends email by the existing `notify.<event>` preferences. A general notification sender stays
    its own piece of work.
14. **Record hand-off.** Dropping a record card onto another calendar changes only the declared
    `resource` field. Record sources without `resource` can't be dropped there.
15. **Free and busy detail.** Overlapping and touching intervals are merged, with no further
    rounding.
