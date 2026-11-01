# Declarative framework reference

This is a small framework example, not a production manufacturing application.
Copy this directory to `PROJECT/apps/amendment` in a new Evlet project, then run:

```sh
evlet build --trust
evlet instance demo
evlet install amendment --instance demo --trust
evlet dev --instance demo --watch
```

Create an administrator in the local browser. Add a Company, Warehouse,
Item Master, matching active BOM and optional Stock Balance. Create a Sales
Order with owned rows. Quantity/rate changes calculate row amounts and the
order total; stock is a virtual query. Save before opening Create Work Orders.
Select eligible rows, quantities and BOMs. Submitting creates related Work
Orders atomically, without changing the source order. Retrying the same
submission key does not create duplicates.

A Quote is a finite-v1 document. Its `payment_days` are suggested from the
selected Company through a Link path and stay editable; `valid_until` adds them
to the issue date. The discount is rounded half up to cents without an explicit
`round()`. The stage is a State: Send (only with a positive total), Mark won,
Mark lost and Reopen (administrators only) are its moves. A sent, won or lost
quote is locked against ordinary edits, except its lost reason, which applies
only to lost quotes. Extend validity is an action with one input that works on
sent quotes. A won quote's discount rate is locked even for actions. An
administrator may void a sent quote with a reason, or revise it: the sent quote
becomes void and keeps its quote number, and a new draft copy with the same lines links
back to it. The copy has no number until it is sent. Companies
have a phone number and quotes have free tags. The quote list declares measures:
the total of open (sent) quotes, the total of won quotes and the number of quotes.

Sales Register is manual. Live Sales Register runs after valid filter changes.
The nested warehouse service demonstrates a read-only custom API and daily
cron declaration. Installing registers the schedule but does not start a
worker. Use the normal `serve --jobs` option when intentionally running tasks.

See `docs/amendment-v2.md` in the framework for authoring, numeric, permission,
packaging, migration and native extension contracts.
