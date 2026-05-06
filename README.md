# Print Farm Maintenance Kiosk Frontend

Compact Flask kiosk app for an 800x480 Raspberry Pi touchscreen, with a SQLAlchemy backend that can run on PostgreSQL.

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

The starter data is seeded into the configured database on first run.

## PostgreSQL Backend

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Create a local PostgreSQL database and user:

```bash
createuser maintenance_kiosk
createdb maintenance_kiosk -O maintenance_kiosk
psql -d maintenance_kiosk -c "ALTER USER maintenance_kiosk WITH PASSWORD 'maintenance_kiosk';"
```

Create `.env` from the example:

```bash
cp .env.example .env
```

Then run the app:

```bash
python3 app.py
```

The app reads `DATABASE_URL`, creates the tables if needed, and seeds the initial printer/demo records only when the `printers` table is empty.

If `DATABASE_URL` is not set, the app falls back to `data/kiosk.db` so the UI can still preview without Postgres. For the MacBook deployment, use PostgreSQL.

The backend code lives in `backend.py`. Flask routes call that repository layer instead of reading in-memory mock data, so weekly maintenance and reactive logs persist.
