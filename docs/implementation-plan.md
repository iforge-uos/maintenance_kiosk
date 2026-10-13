# Maintenance kiosk implementation plan

Updated: 13 October 2026

## Agreed direction

- Staff create and edit maintenance records through the kiosk only. Google Sheets is for shared viewing and reporting.
- The maintenance workbook belongs in Google Drive under `threedprintingiforge@gmail.com`.
- PostgreSQL is not part of the target architecture. Do not upload a PostgreSQL or SQLite database file to Drive as a substitute for the workbook.
- Keep durable local files on the Raspberry Pi for offline records, pending deliveries, cached settings, and cached printer state. These are synchronized with the cloud record.
- Support both email and Discord notifications, with configurable triggers and destinations.
- Provide a Back button on every tab and detail screen, editable settings pages, and a Settings → Testing area.
- Preserve the original style and **800×480 Raspberry Pi touchscreen** resolution. Add the requested functions without redesigning the dashboard or maintenance screens.

This is the agreed target design, preserved as implementation guidance. The first application build now targets Flask, durable local files, Sheets synchronization, notification adapters, Settings, and isolated diagnostics. The PRD and README describe the database-free setup. Capability details below are requirements, not a claim that every milestone is finished; see the implementation status at the end. No live account connection, notification delivery, or real GPIO verification is established by this document.

## Original interface and navigation

The original app is available on `Franklin-vibe-fable` at `da4dc271f55b226cb3ac7f905d49b37a6951f2fe`. Its stylesheet, logos, server-rendered templates, and 800×480 layout are the visual baseline. Preserve the 2×2 dashboard grid with four printers per page, the original ten default printer names and Mainspace/Digital/Exotic labels, eight weekly checks, and 13-week semester. Settings and Testing extend these compact controls; long content scrolls within the screen rather than changing its geometry.

- Add Back using the original header controls. Preserve printer selection and dashboard page when returning from maintenance screens; directly opened pages need a parent fallback.
- Keep the dashboard inside the kiosk when there is no earlier in-app page.
- Settings navigation offers Save draft, Discard changes, and Stay for unsaved configuration or diagnosis edits. Browser drafts remain on the device and do not activate settings. Typed credentials are excluded from drafts and must be stored separately or discarded.
- Core maintenance screens retain normal HTML navigation. Unsaved form edits use the browser's leave-page warning, with explicit form save actions; weekly Continue Later saves an incomplete job for later resumption. The Settings three-choice dialog does not apply to every HTML screen.
- Check browser Back and reload separately from the visible Back controls. Verify behavior at 800×480 and on the actual touchscreen.

## Google Drive and Sheets storage

Use a dedicated Drive folder, for example `Print Farm Maintenance`, containing:

- `Maintenance Records`: the Google Sheets workbook.
- `settings.json`: versioned, non-secret configuration exported by the kiosk.
- `Backups`: dated record/configuration exports, with configurable retention.
- `SOPs`: documents linked from diagnosis results.

The Settings page provides Google connection, connected-account identity, folder selection, workbook selection, and connection status. Prefer user OAuth so files created by the kiosk belong to the connected Google account. An existing workbook shared with a service account is an alternative deployment option. Account access and ownership must be verified during implementation; the email address alone is not authorization or a credential.

### Workbook tabs

| Tab | Row meaning | Contents |
| --- | --- | --- |
| Printers | Latest summary for a printer | Stable printer ID, name, active flag, availability, open incident ID, latest weekly completion, latest repair summary |
| Weekly_Maintenance | Latest state of one weekly job | Stable record ID, revision, printer, technician, academic year, semester, teaching week, checklist values/version, usage readings with units, draft/completed state, notes, timestamps |
| Reactive_Maintenance | Latest state of one incident | Stable incident ID, revision, printer, source, symptom, diagnosis, repair actions, component, technician, incident state, result, repair summary, opened/resolved timestamps |
| Activity_Log | One immutable saved change | Unique change ID, record/incident ID, revision, action, actor, occurrence time, cloud receipt time, and complete saved record snapshot |

Activity_Log is the audit/recovery history. The other tabs are readable current views generated from accepted changes. Corrections happen through the kiosk and create new revisions; previous history remains available. Protect the record tabs against routine manual edits and use filter views for reporting.

For weekly jobs, keep the original eight checklist items, including the filament sensor, and the X/Y/Z movement, filament, and print-hour readings. Record cumulative usage counters by default; calculate differences in reports and provide an explicit counter-reset action. Resume the same weekly job for a printer/year/semester/week instead of creating another job on each save.

For reactive work, retain one incident ID from reporting through resolution. Repeated physical triggers continue an existing open incident. Printer availability (Available, Unavailable, Under Maintenance) is separate from incident state (open, in progress, resolved, escalated, closed unresolved).

### Offline capture and delivery

1. Validate the form or hardware event and generate stable record/change IDs.
2. Write the complete change durably to the Pi's local file queue before acknowledging the save or treating it as accepted.
3. Update the local dashboard and appropriate LEDs from that accepted change.
4. Deliver to Sheets and notification services independently in the background.
5. Persist acknowledgements and retry metadata so a reboot resumes unfinished work.

Use a single serialized Sheets writer for the initial kiosk. Find rows by stable IDs, never stored row numbers. On an ambiguous network timeout, check for the change ID before appending again; reconcile duplicate IDs and incomplete current-view updates. Revisions prevent an older retry from overwriting a newer state. Sheets does not provide unique-key constraints, and exactly-once delivery cannot be assumed.

Show separate status for local saving, Sheets sync, and notifications. Preserve both occurrence time and sync time. Store UTC timestamps; display and schedule in Europe/London by default, with the timezone configurable.

## Editable Settings pages

Ordinary operational changes must be possible without editing Python files. Use validated forms, dropdowns, explanatory labels, Save/Cancel, and versioned configuration history.

| Page | Configurable items |
| --- | --- |
| General | Farm name, timezone, dashboard refresh interval; preserve the original screen style and geometry |
| Printers | Add/rename/archive printers, display order, model, enabled status, notification routing |
| Technicians | Names, active status, optional contact information and notification roles |
| Maintenance forms | Checklist items, labels, units, required fields, presets, quick fixes, result choices |
| Schedule | Academic years, semester dates, teaching weeks, maintenance due dates, reminder windows and quiet hours |
| Diagnosis and SOPs | Categories, questions, answer destinations, diagnoses, actions, SOP file links; validate broken links and graph cycles before activation |
| Google storage | Connect/disconnect account, Drive folder, workbook, sync interval, status and last successful sync |
| Email | Enable/disable, Gmail OAuth or SMTP connection, sender, recipients, event rules, templates |
| Discord | Named webhook destinations, channel routing by printer/event, templates, permitted role mentions, enabled triggers |
| Hardware | GPIO backend, printer-to-button/LED mapping, numbering convention, active polarity, pull-up/down, debounce and hold duration |
| Backup and recovery | Export/import settings, backup schedule, retention, configuration revision history and restore |
| Testing | Integration, data, navigation, notification, queue, and hardware checks described below |

Preserve historical meaning when settings change: checklist definitions carry versions, existing records retain the labels/units used at capture, and printers/technicians are archived rather than removing their history. Validate settings as a complete revision before activation. Indicate whether a change applies immediately or requires restarting the affected worker; reconcile LEDs after hardware changes.

Store ordinary settings in a local atomic configuration file and synchronize a versioned copy to Drive. The kiosk remains the sole settings editor; offline changes synchronize when connectivity returns. Keep Google tokens, SMTP passwords, and webhook URLs in a protected server-side credential store, not in Sheets, ordinary settings exports, source code, or test logs. Settings shows masked credential status and provides replace/disconnect controls.

Changing the workbook/account or a notification destination must account for pending deliveries. Preserve each queued operation's original destination and show outstanding work before a deliberate migration; never silently redirect historical records or pending alerts. The forms configure supported operations; adding an entirely new integration or workflow may still require development.

## Notifications

Configure email and Discord separately. Each event rule selects destinations and whether notification is immediate, grouped into a digest, or disabled.

Suggested defaults:

- New unavailable incident: immediate alert.
- Needs parts or escalation: alert/update.
- Resolution and return to service: resolution message.
- Weekly completion: optional routine channel or digest.
- Overdue maintenance: optional scheduled reminder, deduplicated per printer/reminder window.
- Repeated button presses and ordinary draft saves: no repeated alert.

Include printer, incident ID, original event time, status, summary, technician where known, and an appropriate workbook link. Mark delayed alerts with their occurrence time and current state so an old fault alert does not imply the printer is still down. Keep delivery independent of Sheets availability.

Persist notification IDs, attempts, outcomes, and returned Discord message IDs. Respect rate-limit responses and retry transient failures with backoff. A timeout after a service accepted a message may still produce a duplicate. Report permanent configuration/destination errors in Settings instead of endlessly retrying them.

## Settings → Testing

Provide individual Run test actions and a Run non-sending checks action. Every result includes the destination/device, start time, pass/fail/unsupported status, duration, and a useful redacted error. Never mark a simulated check as a real hardware pass. Hide secrets from output.

| Test | What it verifies |
| --- | --- |
| Google connection | Connected identity, selected folder/workbook access, expected tabs and headers |
| Sheets write/read | Write a uniquely marked row in a dedicated test tab, read it back, verify content, and remove only that test row; report cleanup failure |
| Email | Send a clearly labelled test email to the chosen test recipient, showing transport acceptance separately from confirmed delivery/receipt |
| Discord | Send a clearly labelled test message to the selected webhook, show returned message ID/channel result, and optionally test edit/delete of that exact test message |
| Notification rules | Preview matching recipients/channels and rendered messages for fault, completion, escalation, recovery and overdue cases; send samples only through an explicit action |
| Physical button/input | Arm a short test session for a selected printer, show actual pin transitions, measure the hold interval, verify debounce, and record the result |
| Simulated trigger | Inject an event into the same maintenance handler without claiming to exercise electrical input, wiring, or hardware debounce |
| LED/output | Temporarily exercise a selected LED; restore the normal state on completion, cancellation, timeout, or test failure |
| End-to-end maintenance | Create a test incident, update/resolve it, verify Sheets test records and selected test notifications, and preserve evidence of each stage |
| Offline queue | Simulate service unavailability at the adapter level, record a test event, reconnect, and verify retry without intentionally disconnecting the whole device |
| Restart recovery | Verify that a pending test record survives a controlled worker/app restart and is delivered after recovery |
| Diagnosis/SOPs | Validate graph references, reachability, required content and document availability |
| Navigation/forms | Verify Back, parent fallback, unsaved-change choices, draft resume, required fields and validation errors |

Separate diagnostics from actions that send messages or create remote test records. Each sending action explicitly names its destination and requires the operator to press its test button. Run non-sending checks must not dispatch messages.

Test records use a separate namespace/tab and are excluded from real maintenance reports. Physical-input testing is visibly time-limited and does not silently create real maintenance incidents. A real GPIO test is available only on the Raspberry Pi; cloud environments provide simulation and clearly report unsupported physical checks. Restore production trigger handling when the test session ends.

Test sessions have explicit expiry/cancellation. Cancel or expire undelivered test notifications so reconnecting hours later cannot unexpectedly send them. Suppress role/everyone mentions in test messages by default. Real printer availability and live incidents must remain unchanged by tests.

## Implementation milestones and acceptance

1. Revise the repository PRD and saved cloud installation/startup configuration to remove PostgreSQL and describe Google Sheets, local files, email and Discord. Prepare the required Python clients and mock hardware mode.
2. Build the navigation shell and versioned Settings forms, including account connection and secure credential storage.
3. Implement durable local records, weekly/reactive workflows, Sheets synchronization and read-only reporting tabs.
4. Implement notification routing, email/Discord workers, retry handling, and useful delivery status.
5. Implement hardware mapping, trigger/LED handling, and the full Testing page.
6. Verify on cloud and then real Raspberry Pi hardware: offline saves, restart recovery, repeated submissions, partial sync, notification failures, settings changes, Back navigation and end-to-end incident resolution.

Acceptance requires that a staff member can configure the normal operating workflow and diagnose integrations from Settings without source edits; that maintenance records survive network outages and restarts; and that test actions cannot be confused with production maintenance records. A real GPIO pass requires real Raspberry Pi execution. Missing SOP PDFs remain unfinished content to supply separately.

## Implementation status and follow-up

The build restores the original 800×480 interface while adding local durable record handling, Settings and navigation, Sheets/SMTP/Discord adapters, a separately launched delivery worker, and mock/real hardware interfaces. The original three illustrated HTML SOPs and three Markdown SOPs are restored under `sops/`; they do not supply the graph's separate 23 PDF references. Consult the README and current UI for supported controls. Automated tests use temporary storage and mocked external services.

The following remain deployment prerequisites or follow-up work, and must not be inferred from settings fields alone:

- Connect and verify the real Google account/workbook owned by `threedprintingiforge@gmail.com`; provision OAuth/service-account credentials securely. The initial configuration uses existing workbook/folder IDs rather than a Drive picker or automatic workbook creation.
- Supply SMTP credentials and Discord webhooks; verify real inbox/channel receipt through explicit test actions.
- Install/configure the Pi GPIO backend and run actual button, debounce, LED, and reboot tests on the intended Raspberry Pi. Cloud checks provide simulation only.
- Supply the referenced SOP PDFs or configure valid document links.
- Configure the operating-system services and browser autostart for the physical kiosk.
- Complete automatic Drive backup/settings mirroring and restore, scheduled digests/reminders/quiet hours, richer email routing/templates/role mentions, counter-reset workflows, and any advanced settings controls absent from the initial UI.
- Expand isolated diagnostics into a full end-to-end cloud test workflow and verify touchscreen navigation on the actual display.

Preserve this distinction when reporting progress: an editable field is not proof that the corresponding scheduler or external service has run, and a mock check is not a real hardware/integration pass.
