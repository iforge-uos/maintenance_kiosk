# Print Farm Maintenance Kiosk Frontend

Compact Flask frontend prototype for an 800x480 Raspberry Pi touchscreen.

## Run

```bash
python3 app.py
```

Then open:

```text
http://127.0.0.1:5050
```

## Included V1 Pages

- Dashboard with 10 active printers across 2 pages
- Printer action/detail page with quick fixes
- Weekly maintenance form
- Weekly history list and read-only detail
- Reactive start page
- Diagnosis assistant from `data/diagnosis_tree.json`
- Manual reactive log
- Reactive summary/save form
- Full history
- In-app Markdown SOP viewer

The current data is mock data shaped like the future Postgres-backed service payload.
