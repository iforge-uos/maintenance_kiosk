from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    func,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.engine import Engine, Row


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SQLITE_PATH = BASE_DIR / "data" / "kiosk.db"
OPEN_REACTIVE_STATES = {"open", "in_progress", "escalated"}

metadata = MetaData()
_engine: Engine | None = None


printers_table = Table(
    "printers",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(120), nullable=False),
    Column("display_order", Integer, nullable=False),
    Column("status", String(40), nullable=False),
    Column("reactive_state", String(60), nullable=False),
    Column("current_fault_summary", Text, nullable=False, default=""),
    Column("last_weekly_at", String(20)),
    Column("last_reactive_at", String(20)),
    Column("last_fix_summary", Text, nullable=False, default=""),
    Column("has_open_fault", Boolean, nullable=False, default=False),
    Column("updated_at", String(32), nullable=False),
)

maintenance_events_table = Table(
    "maintenance_events",
    metadata,
    Column("event_id", String(80), primary_key=True),
    Column("printer_id", Integer, ForeignKey("printers.id", ondelete="CASCADE"), nullable=False),
    Column("event_type", String(20), nullable=False),
    Column("source", String(40), nullable=False),
    Column("technician_name", String(120)),
    Column("created_at", String(32), nullable=False),
    Column("updated_at", String(32), nullable=False),
    Column("note", Text),
    Column("summary", Text, nullable=False),
    Column("state", String(60), nullable=False),
)

weekly_details_table = Table(
    "weekly_maintenance_details",
    metadata,
    Column("event_id", String(80), ForeignKey("maintenance_events.event_id", ondelete="CASCADE"), primary_key=True),
    Column("academic_year", String(20), nullable=False),
    Column("semester", String(60), nullable=False),
    Column("week_number", Integer, nullable=False),
    Column("nozzle_debris_brushed", Boolean, nullable=False, default=False),
    Column("wiper_screw_tightened", Boolean, nullable=False, default=False),
    Column("fan_screw_tightened", Boolean, nullable=False, default=False),
    Column("enclosure_debris_cleaned", Boolean, nullable=False, default=False),
    Column("bed_cleaned", Boolean, nullable=False, default=False),
    Column("glue_reapplied", Boolean, nullable=False, default=False),
    Column("enclosure_fan_ok", Boolean, nullable=False, default=False),
    Column("filament_sensor_turned_on", Boolean, nullable=False, default=False),
    Column("x_movement_km", Float),
    Column("y_movement_km", Float),
    Column("z_movement_m", Float),
    Column("filament_m", Float),
    Column("total_print_hours", Float),
)

reactive_events_table = Table(
    "reactive_events",
    metadata,
    Column("event_id", String(80), ForeignKey("maintenance_events.event_id", ondelete="CASCADE"), primary_key=True),
    Column("event_state", String(40), nullable=False),
    Column("origin", String(80)),
    Column("symptom_category", String(120)),
    Column("urgency_state", String(80)),
    Column("issue_summary", Text, nullable=False),
    Column("diagnosis_path", Text),
    Column("likely_cause", Text),
    Column("sop_used", Text),
    Column("action_taken", Text),
    Column("component_involved", Text),
    Column("result_status", String(80)),
    Column("fix_summary", Text),
    Column("resolved_at", String(32)),
)

technicians_table = Table(
    "technicians",
    metadata,
    Column("technician_id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(120), nullable=False),
    Column("email", String(255), nullable=False),
    Column("receives_weekly_reminders", Boolean, nullable=False, default=False),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("created_at", String(32), nullable=False),
    Column("updated_at", String(32), nullable=False),
)

app_settings_table = Table(
    "app_settings",
    metadata,
    Column("setting_key", String(120), primary_key=True),
    Column("setting_value", Text, nullable=False),
    Column("updated_at", String(32), nullable=False),
)

notification_queue_table = Table(
    "notification_queue",
    metadata,
    Column("notification_id", Integer, primary_key=True, autoincrement=True),
    Column("event_id", String(80), ForeignKey("maintenance_events.event_id", ondelete="CASCADE")),
    Column("printer_id", Integer, ForeignKey("printers.id", ondelete="CASCADE")),
    Column("channel", String(40), nullable=False, default="email"),
    Column("notification_type", String(80), nullable=False, default="general"),
    Column("recipient_email", String(255)),
    Column("target_url", Text),
    Column("subject", Text),
    Column("body", Text),
    Column("status", String(30), nullable=False, default="pending"),
    Column("retry_count", Integer, nullable=False, default=0),
    Column("created_at", String(32), nullable=False),
    Column("last_attempt_at", String(32)),
    Column("sent_at", String(32)),
    Column("error_message", Text),
)

sync_queue_table = Table(
    "sync_queue",
    metadata,
    Column("sync_id", Integer, primary_key=True, autoincrement=True),
    Column("event_id", String(80), ForeignKey("maintenance_events.event_id", ondelete="CASCADE")),
    Column("export_type", String(80), nullable=False),
    Column("payload_ref", Text),
    Column("status", String(30), nullable=False, default="pending"),
    Column("retry_count", Integer, nullable=False, default=0),
    Column("created_at", String(32), nullable=False),
    Column("last_attempt_at", String(32)),
    Column("completed_at", String(32)),
    Column("error_message", Text),
)

WEEKLY_BOOLEAN_FIELDS = [
    "nozzle_debris_brushed",
    "wiper_screw_tightened",
    "fan_screw_tightened",
    "enclosure_debris_cleaned",
    "bed_cleaned",
    "glue_reapplied",
    "enclosure_fan_ok",
    "filament_sensor_turned_on",
]


def load_env_file() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def database_url() -> str:
    load_env_file()
    url = os.environ.get("DATABASE_URL")
    if not url:
        DEFAULT_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{DEFAULT_SQLITE_PATH}"
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(database_url(), future=True, pool_pre_ping=True)
    return _engine


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def event_id(prefix: str, printer_id: int) -> str:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    return f"{prefix}-{printer_id}-{stamp}"


def row_to_dict(row: Row[Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row._mapping)


def technician_from_form(form: Any) -> str:
    technician = form.get("technician_name", "").strip()
    if technician == "Other":
        return form.get("other_technician", "").strip() or "Other"
    return technician or "Unknown"


def numeric_value(value: str | None) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def init_database(
    printers: list[dict[str, Any]],
    weekly_records: dict[int, list[dict[str, Any]]],
    history: dict[int, list[dict[str, Any]]],
) -> None:
    metadata.create_all(engine())
    ensure_weekly_detail_columns()
    ensure_notification_queue_columns()
    with engine().begin() as conn:
        existing = conn.execute(select(func.count()).select_from(printers_table)).scalar_one()
        if existing:
            return
        seed_database(conn, printers, weekly_records, history)


def ensure_notification_queue_columns() -> None:
    required_columns = {
        "channel": "VARCHAR(40) DEFAULT 'email' NOT NULL",
        "notification_type": "VARCHAR(80) DEFAULT 'general' NOT NULL",
        "target_url": "TEXT",
    }
    with engine().begin() as conn:
        if engine().dialect.name == "sqlite":
            existing = {row._mapping["name"] for row in conn.execute(text("PRAGMA table_info(notification_queue)"))}
        else:
            rows = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'notification_queue'"
                )
            ).fetchall()
            existing = {row._mapping["column_name"] for row in rows}
        for column_name, column_sql in required_columns.items():
            if column_name not in existing:
                conn.execute(text(f"ALTER TABLE notification_queue ADD COLUMN {column_name} {column_sql}"))


def ensure_weekly_detail_columns() -> None:
    column_sql = "BOOLEAN DEFAULT 0 NOT NULL" if engine().dialect.name == "sqlite" else "BOOLEAN DEFAULT false NOT NULL"
    with engine().begin() as conn:
        if engine().dialect.name == "sqlite":
            existing = {row._mapping["name"] for row in conn.execute(text("PRAGMA table_info(weekly_maintenance_details)"))}
        else:
            rows = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'weekly_maintenance_details'"
                )
            ).fetchall()
            existing = {row._mapping["column_name"] for row in rows}
        if "filament_sensor_turned_on" not in existing:
            conn.execute(text(f"ALTER TABLE weekly_maintenance_details ADD COLUMN filament_sensor_turned_on {column_sql}"))


def seed_database(conn: Any, printers: list[dict[str, Any]], weekly_records: dict[int, list[dict[str, Any]]], history: dict[int, list[dict[str, Any]]]) -> None:
    for index, printer in enumerate(printers, start=1):
        conn.execute(
            insert(printers_table).values(
                id=printer["id"],
                name=printer["name"],
                display_order=index,
                status=printer["status"],
                reactive_state=printer["reactive_state"],
                current_fault_summary=printer["current_fault_summary"],
                last_weekly_at=printer["last_weekly_at"],
                last_reactive_at=printer["last_reactive_at"],
                last_fix_summary=printer["last_fix_summary"],
                has_open_fault=bool(printer["has_open_fault"]),
                updated_at=now_text(),
            )
        )

    seed_technicians(conn)

    for printer_id, records in weekly_records.items():
        for record in records:
            insert_seed_weekly(conn, printer_id, record)

    for printer_id, events in history.items():
        for index, event in enumerate(events, start=1):
            if event["type"] == "reactive":
                insert_seed_reactive(conn, printer_id, index, event)


def seed_technicians(conn: Any) -> None:
    created_at = now_text()
    for number in range(1, 9):
        conn.execute(
            insert(technicians_table).values(
                name=f"Technician {number}",
                email=f"technician{number}@example.com",
                receives_weekly_reminders=False,
                is_active=True,
                created_at=created_at,
                updated_at=created_at,
            )
        )


def insert_seed_weekly(conn: Any, printer_id: int, record: dict[str, Any]) -> None:
    event = record["event_id"]
    conn.execute(
        insert(maintenance_events_table).values(
            event_id=event,
            printer_id=printer_id,
            event_type="weekly",
            source="seed",
            technician_name=record["technician"],
            created_at=record["date"],
            updated_at=record["date"],
            note=record["note"],
            summary=f"Week {record['week_number']} maintenance complete, {record['checks_complete']}/{record['total_checks']} checks",
            state="Complete",
        )
    )
    checklist = record["checklist"]
    metrics = record["metrics"]
    conn.execute(
        insert(weekly_details_table).values(
            event_id=event,
            academic_year=record["academic_year"],
            semester=record["semester"],
            week_number=record["week_number"],
            nozzle_debris_brushed=bool(checklist["Brush off nozzle debris"]),
            wiper_screw_tightened=bool(checklist["Tighten wiper screw"]),
            fan_screw_tightened=bool(checklist["Tighten fan screw"]),
            enclosure_debris_cleaned=bool(checklist["Clean enclosure debris"]),
            bed_cleaned=bool(checklist["Clean bed"]),
            glue_reapplied=bool(checklist["Reapply glue"]),
            enclosure_fan_ok=bool(checklist["KORA enclosure fan function"]),
            filament_sensor_turned_on=bool(checklist.get("Filament sensor turned on", False)),
            x_movement_km=numeric_value(metrics["X movement"].split()[0]),
            y_movement_km=numeric_value(metrics["Y movement"].split()[0]),
            z_movement_m=numeric_value(metrics["Z movement"].split()[0]),
            filament_m=numeric_value(metrics["Filament"].split()[0]),
            total_print_hours=numeric_value(metrics["Total print hours"].split()[0]),
        )
    )


def insert_seed_reactive(conn: Any, printer_id: int, index: int, event: dict[str, Any]) -> None:
    seed_id = f"r-{printer_id}-{index}"
    state = reactive_event_state(event["state"])
    conn.execute(
        insert(maintenance_events_table).values(
            event_id=seed_id,
            printer_id=printer_id,
            event_type="reactive",
            source="seed",
            technician_name=event["technician"],
            created_at=event["at"],
            updated_at=event["at"],
            note="",
            summary=event["summary"],
            state=event["state"],
        )
    )
    conn.execute(
        insert(reactive_events_table).values(
            event_id=seed_id,
            event_state=state,
            issue_summary=event["summary"],
            result_status=event["state"],
            fix_summary=event["summary"] if state == "resolved" else "",
            resolved_at=event["at"] if state == "resolved" else None,
        )
    )


def find_printer(printer_id: int) -> dict[str, Any] | None:
    with engine().connect() as conn:
        row = conn.execute(select(printers_table).where(printers_table.c.id == printer_id)).fetchone()
    return row_to_dict(row)


def active_technicians() -> list[dict[str, Any]]:
    stmt = select(technicians_table).where(technicians_table.c.is_active == True).order_by(technicians_table.c.name)
    with engine().connect() as conn:
        rows = conn.execute(stmt).fetchall()
    return [dict(row._mapping) for row in rows]


def weekly_reminder_technicians() -> list[dict[str, Any]]:
    stmt = (
        select(technicians_table)
        .where(technicians_table.c.is_active == True)
        .where(technicians_table.c.receives_weekly_reminders == True)
        .order_by(technicians_table.c.name)
    )
    with engine().connect() as conn:
        rows = conn.execute(stmt).fetchall()
    return [dict(row._mapping) for row in rows]


def save_technicians(technicians: list[dict[str, Any]]) -> None:
    updated_at = now_text()
    with engine().begin() as conn:
        conn.execute(delete(technicians_table))
        for technician in technicians:
            name = technician["name"].strip()
            email = technician["email"].strip()
            if not name or "@" not in email or "." not in email.split("@")[-1]:
                raise ValueError("Technician name and valid email are required.")
            conn.execute(
                insert(technicians_table).values(
                    name=name,
                    email=email,
                    receives_weekly_reminders=bool(technician.get("receives_weekly_reminders")),
                    is_active=bool(technician.get("is_active", True)),
                    created_at=updated_at,
                    updated_at=updated_at,
                )
            )


def get_app_setting(setting_key: str, default: str = "") -> str:
    with engine().connect() as conn:
        row = conn.execute(
            select(app_settings_table.c.setting_value).where(app_settings_table.c.setting_key == setting_key)
        ).fetchone()
    return row._mapping["setting_value"] if row else default


def save_app_setting(setting_key: str, setting_value: str) -> None:
    updated_at = now_text()
    with engine().begin() as conn:
        existing = conn.execute(
            select(app_settings_table.c.setting_key).where(app_settings_table.c.setting_key == setting_key)
        ).fetchone()
        if existing:
            conn.execute(
                update(app_settings_table)
                .where(app_settings_table.c.setting_key == setting_key)
                .values(setting_value=setting_value, updated_at=updated_at)
            )
        else:
            conn.execute(
                insert(app_settings_table).values(
                    setting_key=setting_key,
                    setting_value=setting_value,
                    updated_at=updated_at,
                )
            )


def get_dashboard_payload(page: int, page_size: int) -> dict[str, Any]:
    with engine().connect() as conn:
        total_printers = conn.execute(select(func.count()).select_from(printers_table)).scalar_one()
        unresolved_count = conn.execute(
            select(func.count()).select_from(printers_table).where(printers_table.c.has_open_fault == True)
        ).scalar_one()
        total_pages = max(1, (total_printers + page_size - 1) // page_size)
        page = min(max(page, 1), total_pages)
        rows = conn.execute(
            select(printers_table)
            .order_by(printers_table.c.display_order)
            .limit(page_size)
            .offset((page - 1) * page_size)
        ).fetchall()

    return {
        "generated_at": datetime.now().strftime("%H:%M:%S"),
        "sync_status": "Postgres" if database_url().startswith("postgresql") else "Local DB",
        "unresolved_count": unresolved_count,
        "printers": [dict(row._mapping) for row in rows],
        "current_page": page,
        "total_pages": total_pages,
        "total_printers": total_printers,
    }


def weekly_records(printer_id: int) -> list[dict[str, Any]]:
    stmt = (
        select(
            maintenance_events_table.c.event_id,
            weekly_details_table.c.academic_year,
            weekly_details_table.c.semester,
            weekly_details_table.c.week_number,
            maintenance_events_table.c.created_at.label("date"),
            maintenance_events_table.c.technician_name.label("technician"),
            maintenance_events_table.c.note,
            *[weekly_details_table.c[field] for field in WEEKLY_BOOLEAN_FIELDS],
        )
        .select_from(maintenance_events_table.join(weekly_details_table))
        .where(maintenance_events_table.c.printer_id == printer_id)
        .order_by(maintenance_events_table.c.created_at.desc())
    )
    with engine().connect() as conn:
        rows = conn.execute(stmt).fetchall()

    records = []
    for row in rows:
        record = dict(row._mapping)
        record["checks_complete"] = sum(1 for field in WEEKLY_BOOLEAN_FIELDS if record[field])
        record["total_checks"] = len(WEEKLY_BOOLEAN_FIELDS)
        records.append(record)
    return records


def find_weekly_record(printer_id: int, event_id_value: str) -> dict[str, Any] | None:
    stmt = (
        select(maintenance_events_table, weekly_details_table)
        .select_from(maintenance_events_table.join(weekly_details_table))
        .where(maintenance_events_table.c.printer_id == printer_id)
        .where(maintenance_events_table.c.event_id == event_id_value)
    )
    with engine().connect() as conn:
        row = conn.execute(stmt).fetchone()

    if not row:
        return None

    record = dict(row._mapping)
    record["date"] = record["created_at"]
    record["technician"] = record["technician_name"]
    record["checklist"] = {
        "Brush off nozzle debris": bool(record["nozzle_debris_brushed"]),
        "Tighten wiper screw": bool(record["wiper_screw_tightened"]),
        "Tighten fan screw": bool(record["fan_screw_tightened"]),
        "Clean enclosure debris": bool(record["enclosure_debris_cleaned"]),
        "Clean bed": bool(record["bed_cleaned"]),
        "Reapply glue": bool(record["glue_reapplied"]),
        "KORA enclosure fan function": bool(record["enclosure_fan_ok"]),
        "Filament sensor turned on": bool(record["filament_sensor_turned_on"]),
    }
    record["metrics"] = {
        "X movement": format_metric(record["x_movement_km"], "km"),
        "Y movement": format_metric(record["y_movement_km"], "km"),
        "Z movement": format_metric(record["z_movement_m"], "m"),
        "Filament": format_metric(record["filament_m"], "m"),
        "Total print hours": format_metric(record["total_print_hours"], "h"),
    }
    return record


def format_metric(value: float | None, unit: str) -> str:
    if value is None:
        return f"- {unit}"
    return f"{value:g} {unit}"


def full_history_events(printer_id: int) -> list[dict[str, Any]]:
    stmt = (
        select(
            maintenance_events_table.c.event_id.label("id"),
            maintenance_events_table.c.event_type.label("type"),
            maintenance_events_table.c.created_at.label("at"),
            maintenance_events_table.c.technician_name.label("technician"),
            maintenance_events_table.c.summary,
            maintenance_events_table.c.state,
        )
        .where(maintenance_events_table.c.printer_id == printer_id)
        .order_by(maintenance_events_table.c.created_at.desc())
    )
    with engine().connect() as conn:
        rows = conn.execute(stmt).fetchall()

    events = []
    for row in rows:
        event = dict(row._mapping)
        event["technician"] = event["technician"] or "Unknown"
        events.append(event)
    return events


def save_weekly_maintenance(
    printer_id: int,
    form: Any,
    checklist_items: list[tuple[str, str]],
    metric_fields: list[tuple[str, str, str]],
    semesters: list[dict[str, str]],
) -> str:
    created_at = now_text()
    new_event_id = event_id("w", printer_id)
    technician = technician_from_form(form)
    semester_id = form.get("semester", "")
    semester = next((item["label"] for item in semesters if item["id"] == semester_id), semester_id or "Semester")
    week_number = int(form.get("week_number", "1"))
    checked = {field: field in form for field, _ in checklist_items}
    complete_count = sum(1 for value in checked.values() if value)
    note = form.get("note", "").strip()
    summary = f"Week {week_number} maintenance complete, {complete_count}/{len(checklist_items)} checks"
    metrics = {field: numeric_value(form.get(field)) for field, _, _ in metric_fields}

    with engine().begin() as conn:
        conn.execute(
            insert(maintenance_events_table).values(
                event_id=new_event_id,
                printer_id=printer_id,
                event_type="weekly",
                source="manual",
                technician_name=technician,
                created_at=created_at,
                updated_at=created_at,
                note=note,
                summary=summary,
                state="Complete",
            )
        )
        conn.execute(
            insert(weekly_details_table).values(
                event_id=new_event_id,
                academic_year="2025/26",
                semester=semester,
                week_number=week_number,
                nozzle_debris_brushed=checked["nozzle_debris_brushed"],
                wiper_screw_tightened=checked["wiper_screw_tightened"],
                fan_screw_tightened=checked["fan_screw_tightened"],
                enclosure_debris_cleaned=checked["enclosure_debris_cleaned"],
                bed_cleaned=checked["bed_cleaned"],
                glue_reapplied=checked["glue_reapplied"],
                enclosure_fan_ok=checked["enclosure_fan_ok"],
                filament_sensor_turned_on=checked["filament_sensor_turned_on"],
                x_movement_km=metrics["x_movement_km"],
                y_movement_km=metrics["y_movement_km"],
                z_movement_m=metrics["z_movement_m"],
                filament_m=metrics["filament_m"],
                total_print_hours=metrics["total_print_hours"],
            )
        )
        conn.execute(
            update(printers_table)
            .where(printers_table.c.id == printer_id)
            .values(last_weekly_at=created_at[:10], updated_at=created_at)
        )
        queue_sync(conn, new_event_id, "weekly_log", created_at)

    return new_event_id


def ensure_reactive_event(printer_id: int, form: Any) -> str:
    existing = open_reactive_event(printer_id)
    if existing:
        return existing["event_id"]

    created_at = now_text()
    new_event_id = event_id("r", printer_id)
    origin = form.get("event_origin", "Technician observed issue")
    symptom = form.get("symptom_category", "")
    urgency = form.get("urgency_state", "Under Maintenance")
    issue = form.get("issue_summary", "").strip() or "Reactive maintenance started"
    note = form.get("note", "").strip()
    printer_status = "Unavailable" if urgency == "Unavailable" else "Under Maintenance"
    printer_name = find_printer(printer_id)["name"]

    with engine().begin() as conn:
        conn.execute(
            insert(maintenance_events_table).values(
                event_id=new_event_id,
                printer_id=printer_id,
                event_type="reactive",
                source="manual",
                technician_name=None,
                created_at=created_at,
                updated_at=created_at,
                note=note,
                summary=issue,
                state="In Progress",
            )
        )
        conn.execute(
            insert(reactive_events_table).values(
                event_id=new_event_id,
                event_state="in_progress",
                origin=origin,
                symptom_category=symptom,
                urgency_state=urgency,
                issue_summary=issue,
            )
        )
        conn.execute(
            update(printers_table)
            .where(printers_table.c.id == printer_id)
            .values(
                status=printer_status,
                reactive_state="In Progress",
                current_fault_summary=issue,
                has_open_fault=True,
                last_reactive_at=created_at[:10],
                updated_at=created_at,
            )
        )
        queue_sync(conn, new_event_id, "reactive_start", created_at)
        queue_google_chat_notification(
            conn,
            new_event_id,
            printer_id,
            "fault_started",
            f"Fault started on {printer_name}: {issue}. Origin: {origin}. Urgency: {urgency}. Time: {created_at}.",
        )

    return new_event_id


def open_reactive_event(printer_id: int) -> dict[str, Any] | None:
    stmt = (
        select(maintenance_events_table.c.event_id, reactive_events_table.c.event_state)
        .select_from(maintenance_events_table.join(reactive_events_table))
        .where(maintenance_events_table.c.printer_id == printer_id)
        .where(reactive_events_table.c.event_state.in_(OPEN_REACTIVE_STATES))
        .order_by(maintenance_events_table.c.created_at.desc())
        .limit(1)
    )
    with engine().connect() as conn:
        row = conn.execute(stmt).fetchone()
    return row_to_dict(row)


def save_reactive_event(
    printer_id: int,
    form: Any,
    source: str,
    save_action: str | None = None,
    diagnosis_node_id: str | None = None,
) -> str:
    created_at = now_text()
    existing = open_reactive_event(printer_id)
    target_event_id = existing["event_id"] if existing else event_id("r", printer_id)
    technician = technician_from_form(form)
    issue = form.get("current_fault") or form.get("issue_summary") or "Reactive maintenance"
    issue = issue.strip()
    action_taken = ", ".join(form.getlist("action_taken")) if hasattr(form, "getlist") else ""
    action_taken = action_taken or form.get("action_taken", "").strip()
    result_status = form.get("result_status", "").strip() or "Still unavailable"
    fix_summary = form.get("fix_summary", "").strip()
    component = form.get("component_involved", "").strip()
    symptom = form.get("symptom_category", "").strip()
    note = form.get("note", "").strip()
    status, reactive_state, has_open_fault, event_state = printer_state_from_result(save_action, result_status)
    resolved_at = created_at if event_state == "resolved" else None
    event_summary = fix_summary if event_state == "resolved" and fix_summary else issue
    printer_name = find_printer(printer_id)["name"]

    with engine().begin() as conn:
        if existing:
            conn.execute(
                update(maintenance_events_table)
                .where(maintenance_events_table.c.event_id == target_event_id)
                .values(
                    source=source,
                    technician_name=technician,
                    updated_at=created_at,
                    note=note,
                    summary=event_summary,
                    state=reactive_state,
                )
            )
            conn.execute(
                update(reactive_events_table)
                .where(reactive_events_table.c.event_id == target_event_id)
                .values(
                    event_state=event_state,
                    symptom_category=symptom or reactive_events_table.c.symptom_category,
                    issue_summary=issue,
                    diagnosis_path=diagnosis_node_id or reactive_events_table.c.diagnosis_path,
                    action_taken=action_taken,
                    component_involved=component,
                    result_status=result_status,
                    fix_summary=fix_summary,
                    resolved_at=resolved_at,
                )
            )
        else:
            conn.execute(
                insert(maintenance_events_table).values(
                    event_id=target_event_id,
                    printer_id=printer_id,
                    event_type="reactive",
                    source=source,
                    technician_name=technician,
                    created_at=created_at,
                    updated_at=created_at,
                    note=note,
                    summary=event_summary,
                    state=reactive_state,
                )
            )
            conn.execute(
                insert(reactive_events_table).values(
                    event_id=target_event_id,
                    event_state=event_state,
                    symptom_category=symptom,
                    issue_summary=issue,
                    diagnosis_path=diagnosis_node_id,
                    action_taken=action_taken,
                    component_involved=component,
                    result_status=result_status,
                    fix_summary=fix_summary,
                    resolved_at=resolved_at,
                )
            )

        conn.execute(
            update(printers_table)
            .where(printers_table.c.id == printer_id)
            .values(
                status=status,
                reactive_state=reactive_state,
                current_fault_summary="" if not has_open_fault else issue,
                last_reactive_at=created_at[:10],
                last_fix_summary=fix_summary or printers_table.c.last_fix_summary,
                has_open_fault=has_open_fault,
                updated_at=created_at,
            )
        )
        queue_sync(conn, target_event_id, "reactive_log", created_at)
        if event_state == "resolved":
            queue_google_chat_notification(
                conn,
                target_event_id,
                printer_id,
                "fault_fixed",
                f"Fault fixed on {printer_name} by {technician}: {fix_summary or event_summary}. Component: {component or 'Not specified'}. Time: {created_at}.",
            )

    return target_event_id


def printer_state_from_result(save_action: str | None, result_status: str) -> tuple[str, str, bool, str]:
    if save_action == "available" or result_status == "Fixed and available":
        return "Available", "Resolved", False, "resolved"
    if save_action == "progress" or result_status == "Temporary fix":
        return "Under Maintenance", "In Progress" if save_action == "progress" else "Temporary fix", True, "in_progress"
    if result_status == "Escalated":
        return "Unavailable", "Escalated", True, "escalated"
    if result_status == "Needs parts":
        return "Unavailable", "Needs parts", True, "open"
    return "Unavailable", result_status or "Still unavailable", True, "open"


def reactive_event_state(state: str) -> str:
    if state == "Resolved":
        return "resolved"
    if state == "Escalated":
        return "escalated"
    if state in {"In Progress", "Temporary fix"}:
        return "in_progress"
    return "open"


def queue_sync(conn: Any, event: str, export_type: str, created_at: str) -> None:
    conn.execute(
        insert(sync_queue_table).values(
            event_id=event,
            export_type=export_type,
            payload_ref=event,
            status="pending",
            created_at=created_at,
        )
    )


def queue_google_chat_notification(conn: Any, event: str, printer_id: int, notification_type: str, body: str) -> None:
    webhook_row = conn.execute(
        select(app_settings_table.c.setting_value).where(app_settings_table.c.setting_key == "google_chat_webhook_url")
    ).fetchone()
    webhook = webhook_row._mapping["setting_value"] if webhook_row else ""
    if not webhook:
        return
    conn.execute(
        insert(notification_queue_table).values(
            event_id=event,
            printer_id=printer_id,
            channel="google_chat",
            notification_type=notification_type,
            target_url=webhook,
            subject=None,
            body=body,
            status="pending",
            retry_count=0,
            created_at=now_text(),
        )
    )


def pending_notification_jobs() -> list[dict[str, Any]]:
    stmt = (
        select(notification_queue_table)
        .where(notification_queue_table.c.status.in_(["pending", "failed"]))
        .order_by(notification_queue_table.c.created_at)
    )
    with engine().connect() as conn:
        rows = conn.execute(stmt).fetchall()
    return [dict(row._mapping) for row in rows]


def printers_due_for_weekly() -> list[dict[str, Any]]:
    current_year, current_week, _ = datetime.now().isocalendar()
    with engine().connect() as conn:
        rows = conn.execute(select(printers_table).order_by(printers_table.c.display_order)).fetchall()

    due_printers = []
    for row in rows:
        printer = dict(row._mapping)
        last_weekly = printer.get("last_weekly_at")
        try:
            last_date = datetime.strptime(last_weekly or "", "%Y-%m-%d")
        except ValueError:
            due_printers.append(printer)
            continue
        weekly_year, weekly_number, _ = last_date.isocalendar()
        if (weekly_year, weekly_number) != (current_year, current_week):
            due_printers.append(printer)
    return due_printers


def weekly_reminder_body(printers: list[dict[str, Any]], kiosk_base_url: str) -> str:
    lines = [
        "Weekly maintenance reminder",
        "",
        "Please complete weekly maintenance for these printers:",
    ]
    for printer in printers:
        lines.append(f"- {printer['name']} (last weekly: {printer['last_weekly_at'] or 'never'})")
    lines.extend(["", f"Kiosk: {kiosk_base_url}"])
    return "\n".join(lines)


def queue_weekly_reminder_emails(kiosk_base_url: str) -> int:
    recipients = weekly_reminder_technicians()
    due_printers = printers_due_for_weekly()
    if not recipients or not due_printers:
        return 0

    created_at = now_text()
    body = weekly_reminder_body(due_printers, kiosk_base_url)
    with engine().begin() as conn:
        for technician in recipients:
            conn.execute(
                insert(notification_queue_table).values(
                    event_id=None,
                    printer_id=None,
                    channel="email",
                    notification_type="weekly_reminder",
                    recipient_email=technician["email"],
                    target_url=None,
                    subject="Weekly maintenance reminder",
                    body=body,
                    status="pending",
                    retry_count=0,
                    created_at=created_at,
                )
            )
    return len(recipients)


def mark_notification_sent(notification_id: int) -> None:
    sent_at = now_text()
    with engine().begin() as conn:
        conn.execute(
            update(notification_queue_table)
            .where(notification_queue_table.c.notification_id == notification_id)
            .values(status="sent", sent_at=sent_at, last_attempt_at=sent_at, error_message=None)
        )


def mark_notification_failed(notification_id: int, error_message: str) -> None:
    failed_at = now_text()
    with engine().begin() as conn:
        row = conn.execute(
            select(notification_queue_table.c.retry_count).where(
                notification_queue_table.c.notification_id == notification_id
            )
        ).fetchone()
        retry_count = int(row._mapping["retry_count"] if row else 0) + 1
        conn.execute(
            update(notification_queue_table)
            .where(notification_queue_table.c.notification_id == notification_id)
            .values(
                status="failed",
                retry_count=retry_count,
                last_attempt_at=failed_at,
                error_message=error_message,
            )
        )
