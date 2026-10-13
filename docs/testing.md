# Validation and integration tests

## Automated checks

From the repository, with its locked dependencies installed:

```sh
.venv/bin/python -m pytest
```

The suite tests local storage/domain rules, HTTP endpoints, integrations using mocked network responses, diagnostic isolation, and GPIO logic using mock input. Tests must use temporary runtime directories and must not call live Google, SMTP, Discord, or GPIO services. Passing these checks does not establish live account access or Raspberry Pi wiring.

## Settings → Testing

Run configuration and diagnosis checks first. The non-sending check group must never send messages, mutate live Sheets records, or create production maintenance incidents.

| Check | Action and interpretation |
| --- | --- |
| Configuration | Validates settings and reports missing connection requirements. |
| Diagnosis/SOPs | Validates the graph and missing or broken local references. Missing repository SOP PDFs are content to supply. |
| Google connection | Checks selected account/workbook/folder access; review actual owner/sharing in Drive. |
| Sheets write/read | Explicitly writes a uniquely marked test row in a separate tab, reads it, and removes that test row. Inspect cleanup failures. |
| Email | Explicitly sends a labelled message to the selected recipient. Acceptance by SMTP is distinct from inbox receipt. |
| Discord | Explicitly sends a labelled test to the selected destination, suppressing mentions. Confirm channel receipt. |
| Simulated trigger | Exercises isolated maintenance handling. Does not validate electrical input, pin wiring, or real debounce. |
| Physical input | Measures actual input on a Pi during a bounded session; unavailable in cloud/mock mode. The diagnostic lease pauses the production watcher. |
| LED | Exercises the real output and restores its previous state; unavailable without supported hardware. |
| Offline persistence | Uses separate temporary test records to check local durability without disconnecting the device. |
| Recovery | Reopens isolated persisted state to check recovery. A real Pi power/reboot test remains necessary. |

External tests are individual operator actions. They do not queue test notifications for later delivery. An unavailable result is not a pass and should explain the missing prerequisite. Results must redact credentials; do not paste secret values into support logs.

## Manual release checks

Use a separate development runtime directory and test workbook/channels for these checks; do not rehearse against real maintenance records.

1. Save an incomplete weekly job, resume it, complete it, and confirm there is one job for the printer/year/semester/week. Verify required checklist items and cumulative metric units.
2. Open a reactive incident, trigger the same printer again, and verify the original incident continues. Resolve it with an explicit availability decision; confirm the dashboard and hardware LED agree.
3. Save from two stale browser sessions and confirm the second update is rejected as a revision conflict rather than silently overwriting changes.
4. Disconnect an integration at its adapter/configuration boundary. Save locally, restart the app and worker, reconnect, and confirm pending changes synchronize without losing history. Do not assume a simulated recovery check proves power-loss durability on physical storage.
5. Check Sheets activity IDs and current-view revisions after a partial/ambiguous request. An older retry must not overwrite a newer row. Verify notification failures do not prevent Sheets progress, or vice versa.
6. Check Home and Back on every tab. Home must open `/`, including from printers on dashboard pages 2 and 3. Back must retrace visited kiosk pages and skip Settings/Testing, preserving printer/page/query selections. Verify repeated Back, reload, native browser Back/Forward, and direct-link fallbacks. A cancelled leave warning must not consume navigation history. Back after a save must use GET without another POST. In Settings, verify Save draft/Discard/Stay for navigation with unsaved edits, draft restoration, and browser Back. Typed credentials must not enter browser drafts. Core maintenance pages use normal HTML navigation and the browser's leave-page warning for unsaved forms; verify that warning and the forms' explicit save actions, including weekly Continue Later and resume. Do not expect the Settings three-choice dialog on every HTML page.
7. Change settings and reload. Verify revision history, invalid-setting errors, archive behavior, and that settings exports contain no secrets. Pending destination changes must not silently redirect existing jobs.
8. On the Pi, test actual short/long/bouncing button presses, LED polarity, restart restoration, and repeated fault presses. Verify the diagnostic lease pauses and resumes production GPIO handling.
9. Confirm Sheets ownership, production channel/recipient routing, test isolation, and actual message receipt with the responsible account owner.

## Original touchscreen layout

Use an **800×480** browser viewport at 100% zoom, then repeat on the intended Raspberry Pi screen. The visual reference is `Franklin-vibe-fable` at `da4dc271f55b226cb3ac7f905d49b37a6951f2fe`; feature additions must preserve its stylesheet, logos, compact layout, and resolution.

- Check the dashboard's fixed 2×2 grid and Previous/Next controls. With the ten default printers, Mainspace, Digital, and Exotic contain four, four, and two printers respectively.
- Check printer actions, the eight-item weekly checklist (including the filament sensor), 13-week selector, weekly history/detail, reactive forms, diagnosis, history, and SOP viewer against the original screens. Back and save controls must remain reachable.
- Visit every Settings section and Testing. Confirm no horizontal overflow, the header and navigation remain accessible, and long forms/results scroll inside their content panes.
- Check restored illustrated SOP images and Back navigation in the viewer. Separately run diagnosis/SOP validation: the 23 PDF references in the graph remain missing until supplied or relinked.

A desktop viewport check does not verify touch accuracy, the on-screen keyboard, or physical display scaling. Confirm those on the Pi before deployment.

## Known verification boundaries

Three illustrated HTML SOPs and three Markdown SOPs from the original app are included. The diagnosis graph still references 23 missing PDF files. Supply those documents or replace their links through Settings before accepting those diagnosis-to-SOP paths.

Live Google authorization, ownership/sharing, SMTP receipt, Discord delivery, and physical GPIO are deployment checks. No build or mocked test outcome should be reported as their successful completion. Scheduled overdue reminders/digests, automatic Drive backup/config uploads, and full cloud end-to-end recovery are separate milestones in the [implementation plan](implementation-plan.md).

## Browser navigation regression check

With Node.js, Playwright and Chromium available, run `node tests/browser_navigation.cjs` from the repository. Set `CHROMIUM_PATH` if Chromium is not at `/usr/bin/chromium`, or `KIOSK_TEST_PYTHON` if the app Python is not `.venv/bin/python`. The check starts its own temporary kiosk with every integration disabled; it does not use the installed kiosk's data.

The browser check covers Home, repeated Back, skipped Settings/Testing, preserved dashboard page selection, reload, browser Back/Forward, unsaved form cancellation, settings drafts, GET navigation after a saved record, new tabs, direct links, and header bounds at 800×480. It also checks that the four printer panels have distinct backgrounds. For physical touch behavior, repeat on the Pi.
