# Product Requirements Document (PRD)
## Print Farm Maintenance Kiosk
**Version:** 1.0  
**Date:** 2026-04-21  
**Status:** Planning / MVP Definition  
**Primary platform:** Raspberry Pi + 7-inch touchscreen + Python/Flask + SQLite  
**Deployment model:** Local-first kiosk with GPIO hardware integration and background sync

---

## 1. Executive Summary

The Print Farm Maintenance Kiosk is a local-first operations system for an 8-printer FDM print farm. It is designed to make maintenance logging fast, visible, and reliable in a student-run environment. The kiosk will run on a Raspberry Pi with a 7-inch touchscreen, connect to physical buttons and LEDs over GPIO, store all records locally in SQLite, upload logs to Google Drive when connectivity is available, and send email notifications to designated technicians when a printer is marked unavailable by hardware trigger.

The system has four main goals:

1. make weekly maintenance logging consistent and easy
2. make reactive maintenance reporting immediate and structured
3. surface printer status and maintenance history clearly on the kiosk home screen
4. reduce downtime by linking diagnosis flows to SOPs and common-fault shortcuts

The product is intentionally optimized for:
- **speed**: target under 30 seconds for most maintenance logging tasks
- **simplicity**: large buttons, minimum typing, dropdowns and checkboxes
- **reliability**: local storage continues working with no internet
- **operational clarity**: any user can see printer state, recent maintenance, and unresolved faults at a glance

---

## 2. Problem Statement

In a shared print farm, maintenance work is often informal and fragmented:
- faults are noticed but not logged consistently
- printers are marked unavailable verbally rather than systematically
- recurring issues are hard to spot
- weekly maintenance may be completed but not recorded in a structured way
- there is no central, touch-friendly place to see what was fixed, when, and by whom
- cloud-only systems are unsuitable because the kiosk must continue working during network outages

The system must solve these problems without adding operational friction.

---

## 3. Product Vision

Create a kiosk-based maintenance system that acts as both:

1. a **live print farm status board**
2. a **fast maintenance logging terminal**

The system should become the single source of truth for:
- current printer availability
- weekly maintenance history
- reactive maintenance history
- unresolved faults
- what was fixed most recently
- technician response trail
- decision-tree guided fault diagnosis
- linked SOP usage

---

## 4. Goals and Success Criteria

### 4.1 Primary goals
- Provide a clear home dashboard showing status of all 8 printers.
- Record weekly maintenance consistently across teaching weeks 1–12 each semester.
- Record reactive maintenance with enough structure to support later analysis.
- Support hardware-triggered unavailable events from GPIO buttons.
- Turn LEDs on/off to mirror printer fault state.
- Send automatic technician email alerts on GPIO-triggered fault events.
- Keep operating fully offline and sync later.
- Surface SOPs and guided diagnosis to reduce troubleshooting variability.

### 4.2 Success metrics
- 100% of weekly maintenance events recorded through the kiosk or explicitly marked missed.
- 100% of GPIO-triggered faults generate a local event record.
- 95%+ successful email delivery attempts, with retry when internet returns.
- Median weekly maintenance logging time under 30 seconds.
- Median quick-fix reactive logging time under 15 seconds.
- Mean time to identify printer state from main page under 5 seconds.
- No data loss during network outage or reboot.
- Ability to retrieve complete history for any printer from the kiosk.

---

## 5. Users and Stakeholders

### 5.1 Primary users
- 3D printing technicians / reps
- trained student staff responsible for printer upkeep

### 5.2 Secondary users
- general print farm users checking availability
- space leads / supervisors
- staff reviewing equipment reliability
- future student teams inheriting the system

### 5.3 Stakeholders
- iForge / print farm operations lead
- technicians receiving alert emails
- future maintainers of the kiosk software
- print farm users relying on status visibility

---

## 6. Scope

### 6.1 In scope (MVP)
- Raspberry Pi kiosk UI
- 7-inch touchscreen optimized interface
- 8-printer dashboard split across 2 pages
- weekly maintenance logging
- weekly maintenance history
- reactive maintenance logging
- reactive diagnosis assistant
- SOP linking from diagnosis
- quick-fix / common-fault shortcut system
- GPIO buttons for printer fault marking
- GPIO LEDs for printer unavailable indication
- automatic event generation on button hold
- automatic email notifications to designated technicians
- local SQLite storage
- upload/export to Google Drive
- kiosk mode autostart on boot
- no-auth system

### 6.2 Out of scope (MVP)
- direct OctoPrint control
- automatic fault detection from printer firmware
- user login / authentication
- mobile app
- multi-site support
- parts inventory management
- advanced analytics dashboards (e.g. Grafana)
- image upload with maintenance events
- role-based permissions
- remote admin editing UI for diagnosis tree

### 6.3 Future scope
- direct link to OctoPrint/Prusa telemetry
- overdue maintenance reminders
- statistics dashboard
- spare parts tracking
- technician workload reporting
- auto-generated semester reports
- web-based admin editor for decision tree JSON
- barcode/QR printer selection
- photo attachments
- calendar integration

---

## 7. Assumptions and Constraints

### 7.1 Assumptions
- Print farm has exactly 8 printers in initial rollout.
- Each printer has one physical fault button and one LED.
- Button press-and-hold duration is 1 second.
- The kiosk will be in a supervised location.
- Technicians are comfortable selecting their name from a dropdown.
- SOP files can be stored locally on the Pi and optionally mirrored on Drive.
- Diagnosis tree and quick-fix presets will be stored in editable JSON files.
- Weekly maintenance is aligned with teaching weeks 1–12 in each semester.

### 7.2 Constraints
- UI must work well on a 7-inch touchscreen.
- Typing must be minimized.
- System must continue functioning without internet.
- Hardware must use Raspberry Pi GPIO directly for MVP.
- There is no user authentication layer.
- The system must be maintainable by future student teams.

---

## 8. Product Principles

1. **Local first:** write to SQLite before attempting any network action.
2. **Fast by design:** dropdowns, checkboxes, large buttons, minimal free text.
3. **Single source of truth:** printer status and maintenance history come from one shared database.
4. **One event model:** button-triggered faults and manually created reactive events use the same reactive event structure.
5. **Transparent operations:** home screen must communicate current state immediately.
6. **Maintainable by editing files:** diagnosis logic and quick-fix presets live outside application code.

---

## 9. Functional Overview

The system consists of five cooperating subsystems:

1. **Kiosk frontend (Flask templates / UI)**  
   Provides dashboard, logging pages, history pages, diagnosis interface, and SOP access.

2. **Application backend (Flask routes + service layer)**  
   Handles business rules, event creation, state transitions, history aggregation, Drive export triggers, and data validation.

3. **SQLite data layer**  
   Stores printer metadata, maintenance logs, diagnosis outputs, notification queue, sync queue, and configuration references.

4. **GPIO watcher service**  
   Monitors buttons, measures hold duration, creates fault events, and sets LEDs.

5. **Email + sync workers**  
   Send queued emails and upload queued exports/files to Google Drive.

---

## 10. User Roles

Because there is no authentication, “roles” are operational rather than enforced in software.

### 10.1 Technician
Can:
- log weekly maintenance
- log reactive maintenance
- continue open reactive events
- use diagnosis assistant
- open SOPs
- review printer history
- resolve faults

### 10.2 General viewer
Can:
- view main page
- see printer status
- see basic history summaries
- not expected to edit, but software does not technically prevent interaction

### 10.3 Maintainer / future developer
Can:
- update printer config
- update technician list
- update diagnosis tree JSON
- update quick-fix JSON
- update email recipient list
- maintain services and deployment

---

## 11. Detailed Requirements

# 11A. Main Page

## 11A.1 Purpose
The main page is the default kiosk view and acts as the operational dashboard.

It must answer:
- Which printers are available?
- Which printers are unavailable?
- Which printers are under maintenance?
- When was each printer last checked weekly?
- When was each printer last repaired reactively?
- What was fixed most recently?
- Which printers currently have unresolved faults?

## 11A.2 Layout
- Header bar at top
- 2×2 printer card grid per page
- 2 pages total for 8 printers
- Page navigation at bottom
- Large touch targets
- No keyboard required

## 11A.3 Header content
- System title: `Print Farm Maintenance Kiosk`
- Current date and time
- Sync status indicator:
  - Synced
  - Local only
  - Upload pending
  - Error
- Alert count:
  - number of unresolved faults
- Page indicator:
  - Page 1 / 2
  - Page 2 / 2

## 11A.4 Printer tile content
Each tile shall show:
- printer name
- current status badge:
  - Available
  - Unavailable
  - Under Maintenance
- last weekly maintenance date
- last reactive maintenance date
- conditional summary:
  - if available: latest fix summary
  - if unavailable: current unresolved fault summary

## 11A.5 Tile interaction
Tapping a tile shall open a printer action menu/page with:
- Weekly Maintenance
- Reactive Maintenance
- Weekly History
- Full History
- Assist (Diagnosis / SOP entry point)

## 11A.6 Sorting / pagination behavior
- Printers appear in fixed farm order.
- Exactly 4 printers per page.
- Previous/Next buttons switch pages.
- Optional later behavior: unresolved printers surfaced first (not required in MVP).

## 11A.7 Backend summary requirements
For each printer card, backend must provide:
- printer_id
- printer_name
- status
- last_weekly_at
- last_reactive_at
- last_fix_summary
- current_fault_summary
- has_open_fault
- updated_at

---

# 11B. Weekly Maintenance

## 11B.1 Purpose
Provide a fast, structured form to record standard weekly maintenance.

## 11B.2 Access
- Main Page → Printer → Weekly Maintenance
- Weekly Maintenance page shall include a `View Weekly History` button in the header

## 11B.3 Weekly maintenance form requirements
The form shall fit on a single screen or near-single-screen layout suitable for a 7-inch display and use large toggles/checks.

### Required checklist items
- Brush off nozzle debris
- Tighten wiper screw
- Tighten fan screw
- Clean enclosure debris
- Clean bed
- Reapply glue
- KORA enclosure fan function

### Required usage metric fields
- X movement (km)
- Y movement (km)
- Z movement (m)
- Filament (m)
- Total print hours

### Required metadata
- Printer
- Academic year
- Semester
- Teaching week number (1–12)
- Date/time auto-filled
- Technician name (dropdown)

### Optional field
- Note

## 11B.4 UX behavior
- Use toggles/checkboxes for checklist items
- Use stepper controls or numeric inputs optimized for touch for metrics
- Technician selected from dropdown
- Optional `Select All` action may be supported
- Optional note presets may be supported:
  - No issues
  - Minor wear observed
  - Needs attention soon

## 11B.5 Save actions
- Save & Mark Complete
- Save & Continue Later
- Cancel

## 11B.6 Validation
At minimum, system shall require:
- printer
- technician
- week number / semester context
- ability to save without note

## 11B.7 Outputs
Saving a weekly maintenance record shall:
- create a maintenance event of type `weekly`
- store checklist and metrics
- update printer.last_weekly_at
- update dashboard summary if needed
- queue export/sync

---

# 11C. Weekly History

## 11C.1 Purpose
Allow technicians to review prior weekly maintenance for a given printer.

## 11C.2 Access path
Preferred path:
- Main Page → Printer → Weekly Maintenance → View Weekly History

System may also support:
- Main Page → Printer → Weekly History

## 11C.3 Weekly history list
List newest records first as touch-friendly cards.

Each record card shall show:
- week number
- semester / academic year
- date
- technician name
- completion count summary (e.g. 7/7 checks)
- note preview

## 11C.4 Weekly history detail
Opening a weekly record shall show:
- printer name
- semester and week
- timestamp
- technician
- checklist states
- usage metrics
- note

Mode shall be read-only in MVP.

---

# 11D. Reactive Maintenance

## 11D.1 Purpose
Allow technicians to record, continue, diagnose, and resolve non-routine faults.

## 11D.2 Core workflow
Reactive maintenance uses a 3-stage workflow:

1. Start
2. Diagnosis Assistant or Manual Log
3. Summary / Save

## 11D.3 Reactive Start page
Fields:
- Printer name
- Current status
- Event origin
- Symptom category
- Urgency / Current state
- Short issue summary
- Optional short note

### Event origin options
- Button-triggered fault
- User reported issue
- Technician observed issue
- Print failure observed
- Other

### Symptom categories (initial)
- Filament not sticking
- Filament not extruding
- Under extrusion
- Prints layer shift
- Bed leveling fail
- Unable to change filament
- Fan not spinning
- Unknown / Other

These categories were grounded in the uploaded Core One troubleshooting table and generic FDM issue patterns such as adhesion failures from bed contamination or Z-offset, under-extrusion from partial clogs, heat creep, and dirty extruder gears. The All3DP troubleshooting guide explicitly identifies adhesion problems from improper Z-offset and contaminated beds, and extrusion problems from partial clogs, heat creep, and dirty extruder gears. fileciteturn1file1L8-L21 fileciteturn1file0L12-L21

### Urgency / current state options
- Unavailable
- Under Maintenance
- Intermittent issue
- Investigation only

### Start page actions
- Start Diagnosis
- Skip to Manual Log
- Cancel

## 11D.4 Open-event continuation
If an unresolved reactive event already exists for the printer, the system shall:
- load that event
- allow continuation rather than creating a duplicate
- clearly indicate event id and current state

---

# 11E. Diagnosis Assistant

## 11E.1 Purpose
Guide technicians through a touch-friendly decision tree and surface relevant SOPs.

## 11E.2 Interaction model
- One question at a time
- 2–5 large answer buttons
- breadcrumb/progress path shown
- diagnosis result card when a terminal node is reached
- SOP suggestions shown on diagnosis result

## 11E.3 Data source
The diagnosis system shall read from an external editable JSON file:
- `diagnosis_tree.json`

## 11E.4 JSON structure
The file shall contain:
- metadata/version
- categories
- nodes
- sops

Supported node types:
- question
- diagnosis

Question nodes define:
- text
- help_text
- options with `next`

Diagnosis nodes define:
- label
- description
- likely_causes
- recommended_actions
- component_involved
- fix_summary
- sop_ids

SOP objects define:
- title
- description
- file path / URL
- tags

## 11E.5 Diagnosis behavior
The system shall:
- start at `category.start_node`
- follow `next` links based on user answers
- display diagnosis node when reached
- allow user to accept diagnosis and continue to summary page

## 11E.6 SOP behavior
- Show top matching SOPs linked by diagnosis node
- Open SOPs from local file path in MVP
- Optional Drive link may also be stored

## 11E.7 Authoring behavior
Future updates to diagnosis logic must be possible by editing JSON only, without code changes.

---

# 11F. Manual Reactive Log

## 11F.1 Purpose
Provide a fast path for experienced technicians who already know the fault.

## 11F.2 Required structured fields
- Event origin
- Symptom category
- Issue summary
- Action taken (multi-select)
- Component involved
- Result / Status
- What was fixed (dashboard summary)
- Technician
- Optional note

## 11F.3 Result / Status options
- Fixed and available
- Temporary fix
- Still unavailable
- Needs parts
- Escalated

## 11F.4 Save actions
- Save & Mark Available
- Save & Keep Unavailable
- Save as In Progress
- Cancel

## 11F.5 Data consistency
Manual log must write the same reactive event structure as diagnosis-assisted log.

---

# 11G. Quick Fix Shortcuts

## 11G.1 Purpose
Allow one-tap prefill for common faults and standard fixes.

## 11G.2 Behavior
Selecting a quick-fix shortcut shall:
- create or load the active reactive event
- prefill symptom category
- prefill issue summary
- prefill action taken
- prefill component involved
- prefill result
- prefill fix summary
- optionally link an SOP
- send user to summary/save page for review

## 11G.3 Storage
Quick-fix shortcuts shall be stored in an external editable JSON file:
- `quick_fixes.json`

## 11G.4 Example uses
- Nozzle clog fixed
- Extruder jam cleared
- Bed re-leveled
- Fan debris cleared
- Filament reloaded

---

# 11H. Full History

## 11H.1 Purpose
Provide a unified per-printer log view across:
- weekly maintenance
- reactive maintenance
- GPIO-triggered fault events

## 11H.2 Display
List events newest first with:
- event type
- date/time
- technician (if known)
- summary
- current state/resolution

---

# 11I. GPIO Hardware Integration

## 11I.1 Purpose
Provide immediate physical unavailable marking for each printer.

## 11I.2 Hardware model
For each printer:
- 1 button input
- 1 LED output

Total initial hardware:
- 8 buttons
- 8 LEDs

## 11I.3 Trigger behavior
When a printer’s button is pressed continuously for 1 second:
- corresponding LED turns on
- printer status changes to `Unavailable`
- reactive maintenance event is auto-created if no open event exists
- event source is set to `gpio_button`
- event summary defaults to `Marked unavailable by hardware button`
- email notification(s) are queued to designated technician(s)

## 11I.4 Resolution behavior
When the technician resolves the event and marks the printer available:
- LED turns off
- printer status changes to `Available`
- event may transition to `resolved`

## 11I.5 GPIO service requirements
A background Python service shall:
- monitor button state
- detect press-and-hold duration
- debounce input
- set LED output state
- write events to SQLite
- queue email notification jobs

The web app shall not be solely responsible for GPIO detection.

---

# 11J. Email Notifications

## 11J.1 Purpose
Notify designated technicians when a printer is hardware-marked unavailable.

## 11J.2 Trigger
At minimum in MVP:
- GPIO button hold creates unavailable event

## 11J.3 Email content
Must include:
- printer name
- timestamp
- event source
- event summary
- current status
- instruction to review/react in kiosk

## 11J.4 Delivery method
- SMTP sender account configured on Raspberry Pi
- credentials stored in environment/config, not hardcoded
- worker process sends queued messages

## 11J.5 Reliability model
Email sending shall be queue-based:
1. create event in DB
2. create notification queue record(s)
3. worker attempts send
4. success/failure stored with retry count

## 11J.6 Recipient configuration
Recipient emails shall be configurable per printer or group via config file.

---

# 11K. Google Drive Upload / Export

## 11K.1 Purpose
Provide cloud backup / shared access without making the system dependent on internet.

## 11K.2 Principle
Local SQLite is the source of truth. Drive upload is secondary.

## 11K.3 MVP approach
- export logs to CSV and/or upload structured files
- upload in background when internet is available
- queue failed uploads for retry

## 11K.4 Upload targets
Possible initial organization:
- weekly logs export
- reactive logs export
- semester summaries
- local copies of diagnostic config backups

## 11K.5 Sync status
Dashboard header shall expose simplified sync state:
- Synced
- Pending
- Local only
- Error

---

## 12. Functional Requirements (Formal)

### FR-01 Dashboard
The system shall display all 8 printers across 2 pages with 4 printers per page.

### FR-02 Printer Summary
The system shall show, per printer, status, last weekly maintenance date, last reactive maintenance date, and either the latest fix summary or current fault summary.

### FR-03 Printer Actions
The system shall allow access to weekly maintenance, reactive maintenance, weekly history, full history, and diagnosis assistant from each printer tile.

### FR-04 Weekly Logging
The system shall allow a technician to complete and save a standardized weekly maintenance record for each printer.

### FR-05 Weekly History
The system shall allow users to review past weekly maintenance records and open a read-only detail view.

### FR-06 Reactive Start
The system shall allow creation of structured reactive events including origin, category, urgency/state, and short issue summary.

### FR-07 Reactive Continuation
The system shall load existing unresolved reactive events for a printer instead of creating duplicates.

### FR-08 Diagnosis Assistant
The system shall run a guided decision tree from an external JSON file and return terminal diagnoses linked to SOPs.

### FR-09 Manual Reactive Log
The system shall allow users to bypass diagnosis and directly create/update a reactive event using structured inputs.

### FR-10 Quick Fix Shortcuts
The system shall support configurable shortcut presets that prefill reactive event fields for common faults.

### FR-11 Unified Event Model
GPIO-triggered faults and manually created reactive events shall use the same reactive event schema.

### FR-12 GPIO Fault Trigger
A 1-second hardware button hold shall mark a printer unavailable, create/update a reactive event, illuminate the associated LED, and queue email notification(s).

### FR-13 LED State
The system shall turn off the LED when the related fault is resolved and the printer returns to available state.

### FR-14 Email Queue
The system shall queue and send notification emails using background worker processing.

### FR-15 Local Storage
The system shall store all records locally in SQLite and continue operating offline.

### FR-16 Drive Sync
The system shall export and upload logs to Google Drive asynchronously with retry logic.

### FR-17 Kiosk Mode
The system shall auto-launch in kiosk mode on boot.

### FR-18 Configuration Files
The system shall use editable external files for diagnosis tree, quick-fix presets, printer GPIO mapping, technician list, and notification recipients.

---

## 13. Non-Functional Requirements

### NFR-01 Usability
- touch-friendly on 7-inch display
- large buttons
- minimal typing
- high contrast status colors
- common actions reachable in 1–2 taps

### NFR-02 Speed
- dashboard load under 2 seconds on local networkless operation
- button-triggered status update visible within 1 second
- weekly/reactive quick logging target under 30 seconds
- common-fault shortcut logging target under 15 seconds

### NFR-03 Reliability
- all events stored locally before any network dependency
- no loss of event records during internet outage
- services auto-restart after crash/reboot
- LED states consistent with printer DB state after reboot/recovery

### NFR-04 Maintainability
- diagnosis and quick-fix updates must not require code changes
- printer-to-GPIO mapping editable in config
- technician list editable in config
- services clearly separated and documented

### NFR-05 Security
- no credentials in source code
- SMTP secrets stored in environment/config files not committed to repo
- Drive credentials secured locally
- physical kiosk assumed in supervised area due to lack of auth

### NFR-06 Data Quality
- standardized dropdown values preferred over free-text where practical
- fix summaries should be normalized for dashboard consistency

### NFR-07 Offline Operation
- if internet is absent, all local UI functions remain available
- notifications/uploads are queued, not discarded

### NFR-08 Observability
- service logs for Flask, GPIO worker, email worker, and sync worker
- error states visible enough for debugging

---

## 14. Information Architecture / Screen Map

1. Main Page
2. Printer Action Menu / Printer Detail
3. Weekly Maintenance Page
4. Weekly History List
5. Weekly History Detail
6. Reactive Start Page
7. Diagnosis Assistant Page
8. Reactive Manual Log Page
9. Reactive Summary / Save Page
10. Full History Page
11. SOP Viewer
12. Optional future admin/config pages (not MVP)

---

## 15. Detailed Screen Flows

### 15.1 Weekly flow
Main Page  
→ Select Printer  
→ Weekly Maintenance  
→ complete checklist + metrics  
→ Save & Mark Complete  
→ return to Main Page

### 15.2 Weekly history flow (preferred)
Main Page  
→ Select Printer  
→ Weekly Maintenance  
→ View Weekly History  
→ Weekly History List  
→ Weekly History Detail

### 15.3 Reactive diagnosis flow
Main Page  
→ Select Printer  
→ Reactive Maintenance  
→ Reactive Start  
→ Start Diagnosis  
→ Diagnosis Assistant  
→ Accept diagnosis  
→ Summary / Save  
→ Save outcome

### 15.4 Reactive manual flow
Main Page  
→ Select Printer  
→ Reactive Maintenance  
→ Reactive Start  
→ Skip to Manual Log  
→ Manual Log  
→ Summary / Save or direct save

### 15.5 Quick fix flow
Main Page  
→ Select Printer  
→ Reactive Maintenance  
→ Quick Fix Shortcut  
→ Summary / Save (prefilled)  
→ Save

### 15.6 Hardware fault flow
Button hold (1 second)  
→ GPIO Worker creates/updates reactive event  
→ LED on  
→ Email queued  
→ Main Page shows printer unavailable  
→ Technician later opens Reactive Maintenance to continue event

---

## 16. System Architecture

## 16.1 Frontend
- Flask-rendered HTML templates
- CSS optimized for kiosk touch UI
- Optional light JS for page transitions, timers, and async actions

## 16.2 Backend services
- Flask web app
- GPIO watcher process
- Email worker process
- Drive sync/export worker process

## 16.3 Data store
- postgresQL database on local disk

## 16.4 Config files
- `diagnosis_tree.json`
- `quick_fixes.json`
- `printers.json` or equivalent hardware mapping file
- `technicians.json` or application config
- `.env` for SMTP / credentials

## 16.5 SOP storage
- Local SOP directory on Pi
- Optional Drive copy for backup

---

## 17. Data Model

### 17.1 Printers
Fields:
- printer_id
- printer_name
- display_order
- status
- page_number
- last_weekly_at
- last_reactive_at
- last_fix_summary
- current_fault_summary
- has_open_fault
- updated_at

### 17.2 Maintenance Events
Common metadata table for all maintenance records:
- event_id
- printer_id
- event_type (`weekly`, `reactive`)
- source (`manual`, `gpio_button`, `quick_fix`, `diagnosis_assistant`)
- technician_name
- created_at
- updated_at
- note

### 17.3 Weekly Maintenance Detail
- event_id
- academic_year
- semester
- week_number
- nozzle_debris_brushed
- wiper_screw_tightened
- fan_screw_tightened
- enclosure_debris_cleaned
- bed_cleaned
- glue_reapplied
- enclosure_fan_ok
- x_movement_km
- y_movement_km
- z_movement_m
- filament_m
- total_print_hours

### 17.4 Reactive Events
- event_id
- event_state (`open`, `in_progress`, `resolved`, `escalated`, `closed_unresolved`)
- origin
- symptom_category
- urgency_state
- issue_summary
- diagnosis_path
- likely_cause
- sop_used
- action_taken
- component_involved
- result_status
- fix_summary
- resolved_at

### 17.5 Notification Queue
- notification_id
- event_id
- printer_id
- recipient_email
- subject
- body
- status (`pending`, `sent`, `failed`)
- retry_count
- created_at
- last_attempt_at
- sent_at
- error_message

### 17.6 Sync Queue
- sync_id
- export_type
- payload_ref / file_path
- status
- retry_count
- created_at
- last_attempt_at
- completed_at
- error_message

### 17.7 Hardware Mapping
May be stored in DB or config file:
- printer_id
- button_gpio
- led_gpio
- notify_emails

---

## 18. Configuration and Editable Files

### 18.1 `diagnosis_tree.json`
Holds diagnosis categories, nodes, and SOP links.

### 18.2 `quick_fixes.json`
Holds one-tap shortcut presets.

### 18.3 `printers.json`
Maps printer identity to GPIO pins and notification recipients.

### 18.4 `technicians.json` or settings table
Defines dropdown list for technician names.

### 18.5 `.env`
Holds SMTP host, port, sender credentials, Drive credentials/path settings if applicable.

---

## 19. State Model

### 19.1 Printer status values
- Available
- Unavailable
- Under Maintenance

### 19.2 Reactive event state values
- open
- in_progress
- resolved
- escalated
- closed_unresolved

### 19.3 Typical status transitions
- Available → Unavailable (button hold or manual issue creation)
- Unavailable → Under Maintenance (technician starts working)
- Under Maintenance → Available (fix completed)
- Under Maintenance → Unavailable (problem persists)
- Any state → escalated (needs parts / higher-level support)

---

## 20. API / Service Responsibilities (Suggested)

### 20.1 Flask endpoints/pages
- dashboard view
- printer summary API
- weekly create
- weekly history list/detail
- reactive create/update
- diagnosis next-node API
- quick-fix apply
- full history list
- SOP viewer
- sync status endpoint

### 20.2 GPIO worker responsibilities
- monitor buttons
- debounce
- detect 1-second hold
- create/update reactive event
- set LED state
- queue notifications
- recover LED state from DB on startup

### 20.3 Email worker responsibilities
- poll pending notifications
- send SMTP email
- update queue state
- retry failed sends

### 20.4 Sync worker responsibilities
- generate CSV exports
- upload to Drive
- update sync queue
- surface sync health

---

## 21. Hardware Requirements

### 21.1 Core hardware
- Raspberry Pi with 40-pin GPIO header
- 7-inch touchscreen
- 8 momentary push buttons
- 8 LEDs
- resistors
- wiring harness / terminal block / breadboard or HAT
- power supply
- enclosure/mounting

### 21.2 GPIO assumption
Initial design assumes direct GPIO is sufficient for 8 buttons + 8 LEDs.
Future expansion may use an I/O expander if desired.

### 21.3 LED behavior
- On = printer unavailable / open hardware fault
- Off = printer available
- Future extension: blinking = under maintenance

---

## 22. Deployment Requirements

### 22.1 Operating environment
- Raspberry Pi OS
- Python virtual environment
- local browser in kiosk mode
- system services managed via `systemd`

### 22.2 Services
- Flask app service
- GPIO worker service
- email worker service
- sync worker service

### 22.3 Boot behavior
On startup:
1. boot OS
2. start background services
3. launch kiosk browser
4. recover printer/LED states from DB
5. show main page

---

## 23. Email Configuration Requirements

### 23.1 Sender setup
- dedicated team mailbox recommended
- SMTP credentials configured locally
- app password / relay credentials stored securely

### 23.2 Queue-first sending model
All email sending must be queue-driven to avoid losing notifications during network problems.

### 23.3 Failure handling
- retain failed jobs
- increment retry count
- allow periodic retry
- surface persistent failures in logs / status

---

## 24. Google Drive Sync Requirements

### 24.1 Sync principle
Drive is backup/share, not source of truth.

### 24.2 Sync objects
- periodic CSV export of weekly maintenance
- periodic CSV export of reactive maintenance
- optional config snapshots
- optional semester archive bundles

### 24.3 Retry behavior
Uploads that fail shall remain queued until successful or manually cleared.

---

## 25. Security and Privacy Considerations

- System has no login, so physical placement matters.
- SMTP and Drive credentials must be stored outside code.
- No sensitive personal data beyond technician names and work emails should be required.
- Audit trail depends on technician dropdown honesty because authentication is absent.
- Kiosk should not expose shell access or config editors to general users.

---

## 26. Risks and Mitigations

### Risk 1: false or incomplete entries due to no authentication
**Mitigation:** dropdown technician names, supervised placement, simple workflows, history visibility.

### Risk 2: network outage blocks email or sync
**Mitigation:** queue-first model, local-first DB writes, retry workers.

### Risk 3: GPIO noise / accidental presses
**Mitigation:** debounce + 1-second hold requirement.

### Risk 4: technicians skip diagnosis and enter inconsistent text
**Mitigation:** structured fields, dropdowns, quick-fix presets, normalized fix summaries.

### Risk 5: future team cannot maintain diagnosis logic
**Mitigation:** external JSON format, documentation, validation script.

### Risk 6: inconsistent LED state after reboot
**Mitigation:** worker restores LED outputs from DB on service startup.

### Risk 7: overly complex UI on 7-inch screen
**Mitigation:** staged flows, large buttons, limit options per view, avoid dense tables.

---

## 27. Acceptance Criteria

The MVP is accepted when the following are true:

1. Main page shows 8 printers across 2 pages with correct status and summary fields.
2. Weekly maintenance can be logged for any printer and viewed later in weekly history.
3. Reactive maintenance can be created manually and via diagnosis assistant.
4. Quick-fix shortcuts prefill reactive forms correctly.
5. Diagnosis assistant reads from `diagnosis_tree.json`.
6. SOP links/files can be opened from diagnosis results.
7. Holding a printer button for 1 second turns on the correct LED and creates a local reactive event.
8. An email notification is queued and sent for hardware-triggered faults.
9. Resolving a reactive event turns off the LED and returns the printer to available state when selected.
10. If internet is unavailable, event creation still works and email/upload jobs remain queued.
11. Logs can be exported/uploaded to Google Drive when connectivity returns.
12. All services restart automatically after reboot.

---

## 28. Milestones / Recommended Build Order

### Phase 1 — Core kiosk UI + DB
- DB schema
- main page
- weekly maintenance
- weekly history

### Phase 2 — Reactive logging
- reactive start
- manual log
- summary/save
- full history

### Phase 3 — Diagnosis + SOP integration
- `diagnosis_tree.json` reader
- diagnosis UI
- SOP viewer
- quick-fix preset engine

### Phase 4 — Hardware integration
- GPIO watcher
- button hold logic
- LED control
- open-event continuation

### Phase 5 — Notifications and sync
- notification queue
- SMTP worker
- Drive sync/export worker

### Phase 6 — Hardening
- systemd services
- reboot recovery
- logging
- config validation
- deployment documentation

---

## 29. Open Questions

1. Should `Under Maintenance` LED behavior differ from `Unavailable` in MVP?
2. Should general users be allowed to navigate beyond the main page?
3. Should weekly metrics be cumulative absolute readings or deltas since last week?
4. What exact file format should Drive export use first: CSV only, or CSV + JSON snapshot?
5. Should there be a simple hidden admin route for editing technician list and printer mappings later?
6. Should unresolved events auto-remind technicians after a time threshold in later phases?
7. Should the system support separate LEDs for warning vs unavailable later?

---

## 30. Appendix A — Initial Weekly Checklist

- Brush off nozzle debris
- Tighten wiper screw
- Tighten fan screw
- Clean enclosure debris
- Clean bed
- Reapply glue
- KORA enclosure fan function
- Record X movement
- Record Y movement
- Record Z movement
- Record filament used
- Record total print hours
- Add note if needed

---

## 31. Appendix B — Initial Reactive Categories

Derived from the uploaded Core One ranking table and general FDM troubleshooting coverage:

- Filament not sticking
- Filament not extruding
- Under extrusion
- Prints layer shift
- Bed leveling fail
- Unable to change filament
- Fan not spinning
- Unknown / Other

Common generic causes relevant to these categories include incorrect nozzle-bed gap, dirty/contaminated build plate, partial nozzle clogs, heat creep, and dirty extruder gears. The uploaded All3DP guide explicitly covers these fault classes as common causes and fixes. fileciteturn1file1L8-L21 fileciteturn1file0L12-L21

---

## 32. Appendix C — Initial Common-Fault Shortcuts

Suggested MVP quick fixes:
- Nozzle clog fixed
- Filament reloaded
- Extruder jam cleared
- Bed cleaned and glue reapplied
- Bed repositioned / re-leveled
- Fan debris removed
- Fan cable reconnected
- Belt tension corrected

---

## 33. Appendix D — Recommended Repository Structure

```text
printfarm-kiosk/
├── app.py
├── gpio_service.py
├── email_worker.py
├── sync_worker.py
├── requirements.txt
├── .env
├── config/
│   ├── diagnosis_tree.json
│   ├── quick_fixes.json
│   ├── printers.json
│   └── technicians.json
├── data/
│   └── kiosk.db
├── sops/
│   ├── clear_nozzle_clog.pdf
│   ├── reload_filament.pdf
│   └── ...
├── templates/
├── static/
└── docs/
```

---

## 34. Final Statement

This PRD defines a local-first, touch-friendly, student-maintainable maintenance management system for an 8-printer farm. It intentionally avoids unnecessary complexity such as authentication and cloud dependency while providing strong operational structure, traceable maintenance history, hardware fault integration, and extensible diagnosis logic.

The product is designed so that:
- technicians can move quickly
- future teams can maintain it
- the farm gains a usable operational memory
- recurring failures become visible rather than anecdotal
