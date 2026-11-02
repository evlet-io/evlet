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

A Quote is a finite-v1 document. Its `valid_until` date adds the selected
Company's payment days to the issue date through a Link path, and its discount
is rounded half up to cents without an explicit `round()`.

Sales Register is manual. Live Sales Register runs after valid filter changes.
The nested warehouse service demonstrates a read-only custom API and daily
cron declaration. Installing registers the schedule but does not start a
worker. Use the normal `serve --jobs` option when intentionally running tasks.

See `docs/amendment-v2.md` in the framework for authoring, numeric, permission,
packaging, migration and native extension contracts.
