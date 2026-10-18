// Run with Node.js and Playwright installed. Uses synthetic files and an offline kiosk.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {mkdirSync} = require('node:fs');
const {chromium} = require('playwright');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const python = process.env.KIOSK_TEST_PYTHON || path.join(root, '.venv/bin/python');
const server = spawn(python, ['-u', '-c', `
import tempfile
import smtplib
import requests
from flask import jsonify
from kiosk import create_app
from werkzeug.serving import make_server

outbound = []
def no_outbound(*args, **kwargs):
    outbound.append(True)
    raise AssertionError('The browser file import test must not contact an external service.')
requests.sessions.Session.request = no_outbound
smtplib.SMTP = no_outbound
smtplib.SMTP_SSL = no_outbound
with tempfile.TemporaryDirectory(prefix='kiosk-file-import-test-') as data:
    app = create_app({'TESTING': True, 'DATA_DIR': data})
    service = app.extensions['kiosk']
    settings = service.get_settings()
    settings['technicians'][0]['name'] = 'Retained technician'
    settings['schedule'].update(academic_year='2032/33', semester=3, week=8)
    settings['google'].update(spreadsheet_id='previous-workbook-123456789', folder_id='retained-folder-123456789', sync_interval=120)
    settings['discord']['destinations'].append({'id': 'reports', 'name': 'Reports', 'enabled': False, 'events': ['weekly_completed'], 'printer_ids': []})
    service.save_settings(settings)
    service.set_secret('discord:reports', 'https://discord.com/api/webhooks/222222222222222222/previous-report-token-fixture-only')
    assert all(not service.get_settings()[k]['enabled'] for k in ('google', 'email', 'discord'))
    @app.get('/browser-test/outbound-count')
    def outbound_count():
        return jsonify(count=len(outbound))
    server = make_server('127.0.0.1', 0, app, threaded=True)
    print('PORT=' + str(server.server_port), flush=True)
    server.serve_forever()
`], {cwd: root, stdio: ['ignore', 'pipe', 'pipe']});

let browser;
let log = '';
server.stderr.on('data', chunk => {log += chunk;});

(async () => {
  const port = await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error(`Server startup timed out: ${log}`)), 15000);
    server.once('error', reject);
    server.once('exit', code => reject(new Error(`Server stopped (${code}): ${log}`)));
    server.stdout.on('data', chunk => {
      const match = String(chunk).match(/PORT=(\d+)/);
      if (match) {clearTimeout(timeout); resolve(match[1]);}
    });
  });
  browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium'});
  const base = `http://127.0.0.1:${port}`;
  const context = await browser.newContext({viewport: {width: 800, height: 480}});
  const externalRequests = [];
  await context.route('**/*', route => {
    if (new URL(route.request().url()).origin !== base) {
      externalRequests.push(new URL(route.request().url()).origin);
      return route.abort();
    }
    return route.continue();
  });
  const page = await context.newPage();
  const errors = [];
  const writes = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (request.method() !== 'GET') writes.push({method: request.method(), path: new URL(request.url()).pathname, body: request.postDataJSON()});
  });
  const section = async name => {
    await page.locator(`a.settings-link[href="/settings?section=${name}"]`).click();
    await page.waitForURL(`${base}/settings?section=${name}`);
  };
  const snapshot = () => page.evaluate(() => fetch('/api/state').then(response => response.json()));
  const file = (name, content, mimeType = 'text/plain') => ({name, mimeType, buffer: Buffer.from(content)});
  const secret = name => page.locator(`.secret-control[data-secret-name="${name}"]`);
  const importInput = name => page.locator(`input[type="file"][data-file-import="${name}"]`);
  const browseFile = async (kind, label, payload, container = page) => {
    const button = container.getByRole('button', {name: `Browse storage for ${label}`, exact: true});
    assert.equal(await button.getAttribute('data-file-browse'), kind);
    const chooserEvent = page.waitForEvent('filechooser');
    await button.click();
    const chooser = await chooserEvent;
    assert.equal(chooser.isMultiple(), false, 'Each import must select one file');
    assert.equal(await chooser.element().getAttribute('data-file-import'), kind);
    assert.equal(await chooser.element().getAttribute('aria-label'), `Choose ${label} file`);
    assert.equal(await chooser.element().isVisible(), false, 'The named button opens the hidden file input');
    await chooser.setFiles(payload);
  };
  const waitInput = async (locator, value) => {
    await locator.evaluate((input, expected) => new Promise((resolve, reject) => {
      const start = performance.now();
      const check = () => {
        if (input.value === expected) return resolve();
        if (performance.now() - start > 5000) return reject(new Error('File import did not fill the selected field.'));
        setTimeout(check, 20);
      };
      check();
    }), value);
  };
  const storeSecret = async name => {
    const response = page.waitForResponse(response => new URL(response.url()).pathname === `/api/secrets/${encodeURIComponent(name)}` && response.request().method() === 'PUT');
    await secret(name).getByRole('button', {name: 'Store credential', exact: true}).click();
    assert.equal((await response).status(), 200);
    await waitInput(secret(name).locator('input[type="password"], textarea'), '');
    assert.equal(await secret(name).locator('.badge').innerText(), 'Stored');
  };
  const saveSettings = async () => {
    const response = page.waitForResponse(response => new URL(response.url()).pathname === '/api/settings' && response.request().method() === 'PUT');
    await page.getByRole('button', {name: 'Save settings', exact: true}).click();
    assert.equal((await response).status(), 200);
    await page.getByText('Settings saved', {exact: true}).waitFor();
  };
  const secrets = ['fixture-oauth-secret-do-not-persist-in-drafts', 'fixture-webhook-token-do-not-persist-in-drafts', 'previous-report-token-fixture-only'];
  const assertNoLeaks = async () => {
    for (const route of ['/api/state', '/api/settings', '/api/settings/export', '/api/settings/history', '/api/export']) {
      const text = await page.evaluate(url => fetch(url).then(response => response.text()), route);
      for (const marker of secrets) assert.equal(text.includes(marker), false, `Credential leaked in ${route}`);
    }
    const browserStorage = await page.evaluate(() => JSON.stringify({local: {...localStorage}, session: {...sessionStorage}, history: history.state}));
    for (const marker of secrets) assert.equal(browserStorage.includes(marker), false, 'Credential leaked in browser persistence');
  };
  const noOverflow = async () => {
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > 800 || document.body.scrollWidth > 800), false);
    const buttons = await page.locator('button[data-file-browse]').all();
    assert.ok(buttons.length, 'The section needs a Browse storage button');
    for (const button of buttons) {
      if (!await button.isVisible()) continue;
      await button.scrollIntoViewIfNeeded();
      const box = await button.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= 801, 'Browse storage must fit the 800 px screen');
      assert.ok(box.height >= 44, 'Browse storage must have a touch target at least 44 px tall');
      assert.match(await button.getAttribute('aria-label') || '', /^Browse storage for \S/);
      assert.equal(await button.innerText(), 'Browse storage');
    }
  };
  const screenshot = async (name, control) => {
    const directory = process.env.KIOSK_FILE_IMPORT_SCREENSHOTS;
    if (!directory) return;
    mkdirSync(directory, {recursive: true});
    await control.getByRole('button', {name: 'Store credential', exact: true}).scrollIntoViewIfNeeded();
    await page.locator('#toast').waitFor({state: 'hidden', timeout: 6000});
    await page.screenshot({path: path.join(directory, name)});
  };
  const pauseFileReads = () => page.evaluate(() => {
    window.originalFileBytesForTest = File.prototype.arrayBuffer;
    window.pendingFileReadsForTest = [];
    File.prototype.arrayBuffer = function () {
      const file = this;
      return new Promise((resolve, reject) => window.pendingFileReadsForTest.push(() => window.originalFileBytesForTest.call(file).then(resolve, reject)));
    };
  });
  const finishFileRead = () => page.evaluate(async () => {
    await window.pendingFileReadsForTest.shift()();
    File.prototype.arrayBuffer = window.originalFileBytesForTest;
    delete window.originalFileBytesForTest;
    delete window.pendingFileReadsForTest;
  });

  const oauth = JSON.stringify({web: {
    client_id: 'browser-test.apps.googleusercontent.com',
    client_secret: secrets[0],
    auth_uri: 'https://accounts.google.com/o/oauth2/auth',
    token_uri: 'https://oauth2.googleapis.com/token',
    redirect_uris: ['http://127.0.0.1:5000/auth/google/callback'],
  }});
  const workbook = '1BrowserFixtureWorkbook_0123456789abcdef';
  const webhook = `https://discord.com/api/webhooks/111111111111111111/${secrets[1]}`;

  await page.goto(`${base}/settings?section=technicians`);
  await page.getByLabel('Name', {exact: true}).first().fill('Edited technician');
  await section('schedule');
  await page.getByLabel('Semester', {exact: true}).fill('2');
  await section('google');
  assert.equal(await page.getByLabel('Connection method', {exact: true}).inputValue(), 'service_account');
  await noOverflow();

  const writesBeforeImport = writes.length;
  await browseFile('spreadsheet_id', 'Workbook ID', file('spreadsheet.txt', `\uFEFFhttps://docs.google.com/spreadsheets/d/${workbook}/edit#gid=0\r\n`));
  await waitInput(page.getByLabel('Workbook ID', {exact: true}), workbook);
  assert.equal((await snapshot()).settings.google.spreadsheet_id, 'previous-workbook-123456789');
  await browseFile('google_oauth_client', 'Google OAuth client JSON', file('client.json', `\uFEFF${oauth}`, 'application/json'));
  await page.waitForFunction(marker => document.querySelector('[data-secret-name="google_oauth_client"] textarea').value.includes(marker), secrets[0]);
  assert.equal(writes.length, writesBeforeImport, 'Choosing files must not send their content to the server');
  assert.equal(await page.getByLabel('Enable Google Sheets synchronization', {exact: true}).isChecked(), false);
  assert.equal(await page.getByLabel('Connection method', {exact: true}).inputValue(), 'service_account');
  console.log('PASS Browse storage opens the single-file chooser; Google JSON and workbook URL files fill only their selected fields without sending requests');

  for (const control of ['#home-button', '#back-button']) {
    await page.locator(control).click();
    await page.locator('#leave-dialog').waitFor({state: 'visible'});
    assert.equal(await page.locator('#leave-dialog').getByRole('button', {name: 'Save draft', exact: true}).isDisabled(), true);
    await page.locator('#leave-dialog').getByRole('button', {name: 'Stay', exact: true}).click();
    assert.equal(new URL(page.url()).search, '?section=google');
  }
  await assertNoLeaks();
  await storeSecret('google_oauth_client');
  assert.equal((await snapshot()).settings.google.spreadsheet_id, 'previous-workbook-123456789');
  await saveSettings();
  let saved = await snapshot();
  assert.equal(saved.settings.google.spreadsheet_id, workbook);
  assert.equal(saved.settings.google.folder_id, 'retained-folder-123456789');
  assert.equal(saved.settings.google.sync_interval, 120);
  assert.equal(saved.settings.google.auth_mode, 'service_account');
  assert.equal(saved.settings.google.enabled, false);
  assert.equal(saved.settings.technicians[0].name, 'Edited technician');
  assert.equal(saved.settings.schedule.semester, 2);
  assert.equal(saved.settings.schedule.academic_year, '2032/33');
  assert.equal(saved.settings.schedule.week, 8);
  await page.reload();
  await secret('google_oauth_client').locator('.badge').getByText('Stored', {exact: true}).waitFor();
  assert.equal(await secret('google_oauth_client').locator('textarea').inputValue(), '');
  await screenshot('google-oauth-import-800x480.png', secret('google_oauth_client'));
  console.log('PASS Store credential and Save settings are separate; technician, semester and unrelated settings survive');

  const oauthInput = secret('google_oauth_client').locator('textarea');
  const oauthFile = secret('google_oauth_client').locator('input[type="file"]');
  await oauthFile.setInputFiles(file('client.json', oauth, 'application/json'));
  await page.waitForFunction(marker => document.querySelector('[data-secret-name="google_oauth_client"] textarea').value.includes(marker), secrets[0]);
  const pendingOAuth = await oauthInput.inputValue();
  for (const rejected of [
    file('invalid.json', '{"secret":"MALFORMED_PRIVATE_MARKER",', 'application/json'),
    file('wrong-kind.json', '{"spreadsheet_id":"wrong-file-private-marker"}', 'application/json'),
    file('too-large.json', 'x'.repeat(65537), 'application/json'),
  ]) {
    await page.locator('#toast').evaluate(node => {node.hidden = true; node.textContent = '';});
    await oauthFile.setInputFiles(rejected);
    await page.locator('#toast.error').waitFor({state: 'visible'});
    assert.equal(await oauthInput.inputValue(), pendingOAuth, 'A rejected file must preserve the pending credential');
    const message = await page.locator('#toast').innerText();
    assert.equal(message.includes('MALFORMED_PRIVATE_MARKER'), false);
    assert.equal(message.includes('wrong-file-private-marker'), false);
  }
  await secret('google_oauth_client').getByRole('button', {name: 'Clear input', exact: true}).click();
  await waitInput(oauthInput, '');
  console.log('PASS malformed, wrong-kind and oversized files retain pending input and show redacted errors');

  // A save must not replace the form underneath a file picker that is still open.
  let releaseSave;
  let saveStarted;
  const pendingSave = new Promise(resolve => {saveStarted = resolve;});
  const saveGate = new Promise(resolve => {releaseSave = resolve;});
  const holdSettingsSave = async route => {
    if (route.request().method() === 'PUT') {
      saveStarted();
      await saveGate;
    }
    await route.continue();
  };
  await page.route('**/api/settings', holdSettingsSave);
  const heldResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/settings' && response.request().method() === 'PUT');
  await page.getByLabel('Sync interval (seconds)', {exact: true}).fill('121');
  await page.getByRole('button', {name: 'Save settings', exact: true}).click();
  await pendingSave;
  try {
    assert.equal(await page.locator('.settings-layout').evaluate(node => node.inert), true);
    await page.locator('#toast').evaluate(node => {node.hidden = true; node.textContent = '';});
    // File chooser completion can dispatch change after a save has started.
    await oauthFile.setInputFiles(file('chosen-during-save.json', oauth, 'application/json'));
    await page.locator('#toast').waitFor({state: 'visible'});
    assert.match(await page.locator('#toast').innerText(), /save|saving/i);
    assert.equal(await oauthInput.inputValue(), '');
  } finally {
    releaseSave();
  }
  assert.equal((await heldResponse).status(), 200);
  await page.getByText('Settings saved', {exact: true}).waitFor();
  await page.unroute('**/api/settings', holdSettingsSave);
  assert.equal(await page.locator('.settings-layout').evaluate(node => node.inert), false);
  assert.equal((await snapshot()).settings.google.sync_interval, 121);
  await oauthFile.setInputFiles(file('chosen-after-save.json', oauth, 'application/json'));
  await page.waitForFunction(marker => document.querySelector('[data-secret-name="google_oauth_client"] textarea').value.includes(marker), secrets[0]);
  await storeSecret('google_oauth_client');
  console.log('PASS imports cannot race a settings save; the picker works again after the save completes');

  // An early Save must not submit the previous workbook while its file is reading.
  await pauseFileReads();
  const delayedWorkbook = '1DelayedWorkbookFile_0123456789abcdef';
  const beforeWorkbookSave = writes.length;
  await importInput('spreadsheet_id').setInputFiles(file('slow-workbook.txt', delayedWorkbook));
  await page.waitForFunction(() => window.pendingFileReadsForTest.length === 1);
  await page.getByRole('button', {name: 'Save settings', exact: true}).click();
  await page.locator('#toast').waitFor({state: 'visible'});
  assert.match(await page.locator('#toast').innerText(), /read|file|wait/i);
  assert.equal(writes.length, beforeWorkbookSave, 'Save must not send the old workbook while a file is reading');
  assert.equal((await snapshot()).settings.google.spreadsheet_id, workbook);
  await finishFileRead();
  await waitInput(page.getByLabel('Workbook ID', {exact: true}), delayedWorkbook);
  await saveSettings();
  assert.equal((await snapshot()).settings.google.spreadsheet_id, delayedWorkbook);
  console.log('PASS Save waits for the selected workbook file and cannot silently submit its previous ID');

  // A slow browser file read must not restore a credential after the user clears it.
  await pauseFileReads();
  await oauthFile.setInputFiles(file('slow-client.json', oauth, 'application/json'));
  await page.waitForFunction(() => window.pendingFileReadsForTest.length === 1);
  assert.equal(await secret('google_oauth_client').getByRole('button', {name: 'Store credential', exact: true}).isDisabled(), true);
  await secret('google_oauth_client').getByRole('button', {name: 'Clear input', exact: true}).click();
  await finishFileRead();
  await waitInput(oauthInput, '');
  await section('discord');
  assert.equal(await page.locator('#leave-dialog').isVisible(), false, 'Cleared file must not leave a hidden pending credential');
  console.log('PASS cancelling a slow file read cannot restore a stale credential');

  await noOverflow();
  const discordControl = secret('discord:maintenance');
  const discordInput = discordControl.locator('input[type="password"]');
  const beforeWebhookWrites = writes.length;
  await browseFile('discord_webhook', 'Discord webhook URL', file('webhook.txt', `\uFEFF${webhook}\r\n`), discordControl);
  await waitInput(discordInput, webhook);
  assert.equal(writes.length, beforeWebhookWrites);
  assert.equal(await secret('discord:reports').locator('input[type="password"]').inputValue(), '');
  assert.equal(await secret('discord:reports').locator('.badge').innerText(), 'Stored');
  assert.equal(await page.getByLabel('Enable Discord notifications', {exact: true}).isChecked(), false);
  await discordControl.locator('input[type="file"]').setInputFiles(file('wrong.txt', `https://evil.invalid/api/webhooks/111111111111111111/${secrets[1]}`));
  await page.locator('#toast.error').waitFor({state: 'visible'});
  assert.equal(await discordInput.inputValue(), webhook);
  assert.equal((await page.locator('#toast').innerText()).includes(secrets[1]), false);
  await storeSecret('discord:maintenance');
  const webhookWrites = writes.slice(beforeWebhookWrites);
  assert.equal(webhookWrites.length, 1);
  assert.equal(decodeURIComponent(webhookWrites[0].path), '/api/secrets/discord:maintenance');
  assert.equal(webhookWrites[0].body.value, webhook);
  saved = await snapshot();
  assert.equal(saved.secret_status['discord:maintenance'], true);
  assert.equal(saved.secret_status['discord:reports'], true);
  assert.equal(saved.settings.discord.enabled, false);
  await screenshot('discord-webhook-import-800x480.png', discordControl);
  console.log('PASS webhook text import stores only the selected destination and does not enable notifications');

  await discordControl.locator('input[type="file"]').setInputFiles(file('pending-webhook.txt', webhook));
  await waitInput(discordInput, webhook);
  await page.locator('#home-button').click();
  assert.equal(await page.locator('#leave-dialog').getByRole('button', {name: 'Save draft', exact: true}).isDisabled(), true);
  await page.locator('#leave-dialog').getByRole('button', {name: 'Discard changes', exact: true}).click();
  await page.waitForURL(`${base}/`);
  await page.goto(`${base}/settings?section=discord`);
  await secret('discord:maintenance').waitFor();
  assert.equal(await secret('discord:maintenance').locator('input[type="password"]').inputValue(), '');
  assert.equal(await secret('discord:maintenance').locator('.badge').innerText(), 'Stored');
  await page.getByRole('button', {name: '+ Add destination', exact: true}).click();
  const newDestination = page.locator('.secret-control[data-secret-name^="discord:"]').last();
  assert.equal(await newDestination.getByRole('button', {name: 'Browse storage for Discord webhook URL', exact: true}).isDisabled(), true);
  assert.equal(await newDestination.locator('input[type="file"]').isDisabled(), true);
  await saveSettings();
  assert.equal(await newDestination.getByRole('button', {name: 'Browse storage for Discord webhook URL', exact: true}).isDisabled(), false);
  console.log('PASS new Discord destinations enable Browse storage only after Save settings');
  await assertNoLeaks();
  assert.equal((await snapshot()).jobs.length, 0);
  assert.deepEqual(externalRequests, []);
  assert.equal(await page.evaluate(() => fetch('/browser-test/outbound-count').then(response => response.json()).then(result => result.count)), 0);
  assert.deepEqual(errors, []);
  console.log('PASS discarded imports stay out of browser storage and exports; no external calls, queued jobs or browser errors');
  console.log('PASS Google and Discord import controls fit the original 800x480 screen');
})().catch(error => {console.error(error); process.exitCode = 1;}).finally(async () => {
  if (browser) await browser.close();
  server.kill('SIGTERM');
});
