const TAB_BY_TYPE = {
  printer_status: "Printer Status",
  weekly_log: "Weekly Maintenance Log",
  reactive_log: "Reactive Maintenance Log",
  sync_error: "Sync Errors",
};

const HEADERS = {
  printer_status: [
    "printer_id",
    "printer_name",
    "availability",
    "reactive_state",
    "active_problem",
    "recent_fault",
    "last_weekly_at",
    "last_reactive_at",
    "nozzle_life_used_km",
    "nozzle_life_percent",
    "nozzle_life_label",
    "action_needed",
    "has_open_fault",
    "updated_at",
    "exported_at",
  ],
  weekly_log: [
    "event_id",
    "printer_id",
    "printer_name",
    "source",
    "technician",
    "created_at",
    "updated_at",
    "note",
    "summary",
    "state",
    "academic_year",
    "semester",
    "week_number",
    "nozzle_debris_brushed",
    "wiper_screw_tightened",
    "fan_screw_tightened",
    "enclosure_debris_cleaned",
    "bed_cleaned",
    "glue_reapplied",
    "enclosure_fan_ok",
    "filament_sensor_turned_on",
    "x_movement_km",
    "y_movement_km",
    "z_movement_m",
    "filament_m",
    "total_print_hours",
    "exported_at",
  ],
  reactive_log: [
    "event_id",
    "printer_id",
    "printer_name",
    "source",
    "technician",
    "created_at",
    "updated_at",
    "note",
    "summary",
    "state",
    "event_state",
    "origin",
    "symptom_category",
    "urgency_state",
    "issue_summary",
    "diagnosis_path",
    "likely_cause",
    "sop_used",
    "action_taken",
    "component_involved",
    "result_status",
    "fix_summary",
    "resolved_at",
    "exported_at",
  ],
  sync_error: ["at", "type", "event_id", "payload_ref", "error"],
};

function doPost(event) {
  try {
    const payload = JSON.parse((event.postData && event.postData.contents) || "{}");
    const expectedSecret = PropertiesService.getScriptProperties().getProperty("WEBHOOK_SECRET");

    if (!expectedSecret || payload.secret !== expectedSecret) {
      return jsonResponse({ ok: false, error: "Invalid webhook secret" });
    }

    const type = payload.type;
    const rows = payload.rows || [];
    if (!TAB_BY_TYPE[type]) {
      return jsonResponse({ ok: false, error: `Unknown sync type: ${type}` });
    }

    if (type === "printer_status") {
      upsertRows(type, rows, "printer_id");
    } else {
      appendRows(type, rows);
    }

    return jsonResponse({ ok: true, type, row_count: rows.length });
  } catch (error) {
    return jsonResponse({ ok: false, error: String(error) });
  }
}

function appendRows(type, rows) {
  const sheet = sheetForType(type);
  const headers = HEADERS[type];
  ensureHeaders(sheet, headers);
  if (!rows.length) {
    return;
  }
  sheet
    .getRange(sheet.getLastRow() + 1, 1, rows.length, headers.length)
    .setValues(rows.map((row) => headers.map((header) => valueForCell(row[header]))));
}

function upsertRows(type, rows, keyHeader) {
  const sheet = sheetForType(type);
  const headers = HEADERS[type];
  ensureHeaders(sheet, headers);

  const keyColumn = headers.indexOf(keyHeader) + 1;
  const lastRow = sheet.getLastRow();
  const existingKeys = {};
  if (lastRow > 1) {
    const values = sheet.getRange(2, keyColumn, lastRow - 1, 1).getValues();
    values.forEach((value, index) => {
      if (value[0] !== "") {
        existingKeys[String(value[0])] = index + 2;
      }
    });
  }

  rows.forEach((row) => {
    const rowValues = headers.map((header) => valueForCell(row[header]));
    const key = String(row[keyHeader]);
    const existingRow = existingKeys[key];
    if (existingRow) {
      sheet.getRange(existingRow, 1, 1, headers.length).setValues([rowValues]);
    } else {
      sheet.appendRow(rowValues);
    }
  });
}

function sheetForType(type) {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const tabName = TAB_BY_TYPE[type];
  return spreadsheet.getSheetByName(tabName) || spreadsheet.insertSheet(tabName);
}

function ensureHeaders(sheet, headers) {
  const existing = sheet.getRange(1, 1, 1, headers.length).getValues()[0];
  const needsHeader = existing.some((value, index) => value !== headers[index]);
  if (needsHeader) {
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
    sheet.setFrozenRows(1);
  }
}

function valueForCell(value) {
  if (value === null || value === undefined) {
    return "";
  }
  return value;
}

function jsonResponse(body) {
  return ContentService.createTextOutput(JSON.stringify(body)).setMimeType(ContentService.MimeType.JSON);
}
