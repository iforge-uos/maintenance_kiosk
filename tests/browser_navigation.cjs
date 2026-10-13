// Run with Node.js and Playwright installed. Uses a temporary offline kiosk.
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const {chromium} = require('playwright');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const python = process.env.KIOSK_TEST_PYTHON || path.join(root, '.venv/bin/python');
const server = spawn(python, ['-u', '-c', `
import tempfile
from kiosk import create_app
from werkzeug.serving import make_server
with tempfile.TemporaryDirectory(prefix='kiosk-navigation-test-') as data:
    app = create_app({'TESTING': True, 'DATA_DIR': data})
    assert all(not app.extensions['kiosk'].get_settings()[k]['enabled'] for k in ('google', 'email', 'discord'))
    server = make_server('127.0.0.1', 0, app, threaded=True)
    print('PORT=' + str(server.server_port), flush=True)
    server.serve_forever()
`], {cwd:root, stdio:['ignore','pipe','pipe']});
let browser;
let log = '';
server.stderr.on('data', chunk => {log += chunk;});
(async () => {
  const port = await new Promise((resolve,reject) => {
    const timeout = setTimeout(() => reject(new Error(`Server startup timed out: ${log}`)), 15000);
    server.once('error', reject);
    server.once('exit', code => reject(new Error(`Server stopped (${code}): ${log}`)));
    server.stdout.on('data', chunk => {const match = String(chunk).match(/PORT=(\d+)/); if(match){clearTimeout(timeout);resolve(match[1]);}});
  });
  browser = await chromium.launch({headless:true, executablePath:process.env.CHROMIUM_PATH || '/usr/bin/chromium'});
  const base = `http://127.0.0.1:${port}`;
  const errors = [];
  const context = await browser.newContext({viewport:{width:800,height:480}});
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  const go = async route => {await page.goto(base+route); await page.waitForLoadState('load');};
  const click = async (selector,route) => {await page.locator(selector).first().click(); await page.waitForURL(base+route); await page.waitForLoadState('load');};
  const back = route => click('a[data-back]',route);
  const home = () => click('a[data-home]','/');
  const printer = '/printers/core-one-01';
  await go('/');
  assert.equal(await page.locator('a[data-back]').getAttribute('aria-disabled'),'true');
  await click(`[href="${printer}"]`,printer);
  await click(`[href="${printer}/weekly"]`,printer+'/weekly');
  await click(`[href="${printer}/weekly/history"]`,printer+'/weekly/history');
  await back(printer+'/weekly');
  await back(printer);
  await back('/');
  console.log('PASS repeated Back unwinds real visited pages');

  await click('[href="/dashboard?page=2"]','/dashboard?page=2');
  await click('[href="/printers/core-one-05"]','/printers/core-one-05');
  await page.reload();
  await back('/dashboard?page=2');
  await home();
  await back('/dashboard?page=2');
  console.log('PASS reload, dashboard page and Home always returns to /');

  await home();
  await click(`[href="${printer}"]`,printer);
  await click(`[href="${printer}/weekly"]`,printer+'/weekly');
  await page.goBack();
  await page.waitForURL(base+printer);
  await page.goForward();
  await page.waitForURL(base+printer+'/weekly');
  await back(printer);
  await back('/');
  console.log('PASS native Back/Forward restores navigation snapshots');

  await click(`[href="${printer}"]`,printer);
  await home();
  await click('a[href="/settings"]','/settings');
  await page.getByLabel('Farm name',{exact:true}).waitFor();
  await click('a.settings-link[href="/settings?section=google"]','/settings?section=google');
  await click('.header-actions a[href="/testing"]','/testing');
  await page.getByRole('button',{name:'Run local checks',exact:true}).waitFor();
  await page.locator('#back-button').click();
  await page.waitForURL(base+'/');
  await back(printer);
  console.log('PASS Settings/Testing are omitted from Back');

  await home();
  await click('a[href="/settings"]','/settings');
  await page.getByLabel('Farm name',{exact:true}).fill('Unsaved navigation test');
  await page.locator('#home-button').click();
  await page.getByRole('button',{name:'Stay',exact:true}).click();
  assert.equal(new URL(page.url()).pathname,'/settings');
  assert.equal(await page.getByLabel('Farm name',{exact:true}).inputValue(),'Unsaved navigation test');
  await page.locator('#back-button').click();
  await page.getByRole('button',{name:'Save draft',exact:true}).click();
  await page.waitForURL(base+'/');
  await click('a[href="/settings"]','/settings');
  assert.equal(await page.getByLabel('Farm name',{exact:true}).inputValue(),'Unsaved navigation test');
  await page.locator('#home-button').click();
  await page.locator('#leave-dialog').getByRole('button',{name:'Discard changes',exact:true}).click();
  await page.waitForURL(base+'/');
  console.log('PASS admin Home/Back protect unsaved edits and browser drafts');

  await click(`[href="${printer}"]`,printer);
  await click(`[href="${printer}/weekly"]`,printer+'/weekly');
  await page.locator('[name="note"]').fill('Unsaved maintenance');
  const originalTrail = await page.evaluate(() => history.state.iforgeNavigation.trail);
  page.once('dialog', dialog => dialog.dismiss());
  await page.locator('a[data-back]').click();
  await page.waitForTimeout(100);
  assert.equal(new URL(page.url()).pathname,printer+'/weekly');
  assert.deepEqual(await page.evaluate(() => history.state.iforgeNavigation.trail),originalTrail);
  page.once('dialog', dialog => dialog.accept());
  await back(printer);
  await back('/');
  console.log('PASS cancelled native leave warning does not consume Back history');

  await click(`[href="${printer}"]`,printer);
  await click(`[href="${printer}/weekly"]`,printer+'/weekly');
  await page.locator('[name="note"]').fill('Saved navigation fixture');
  let posts = 0;
  page.on('request',req => {if(req.method()==='POST' && req.url().includes('/weekly'))posts++;});
  await page.getByRole('button',{name:'Continue Later',exact:true}).click();
  await page.waitForURL(base+printer+'/weekly/history');
  await back(printer+'/weekly');
  assert.equal(posts,1);
  assert.equal(await page.locator('[name="note"]').inputValue(),'Saved navigation fixture');
  assert.equal((await page.evaluate(() => fetch('/api/state').then(r=>r.json()))).records.length,1);
  console.log('PASS Back after a save uses GET and does not duplicate a record');

  const fresh = await context.newPage();
  await fresh.goto(base+printer+'/weekly');
  await fresh.locator('a[data-back]').click();
  await fresh.waitForURL(base+printer);
  await fresh.locator('a[data-back]').click();
  await fresh.waitForURL(base+'/');
  await fresh.close();
  const popupPromise = page.waitForEvent('popup');
  await page.evaluate(url => window.open(url,'_blank'),base+printer+'/history');
  const popup = await popupPromise;
  await popup.waitForLoadState();
  await popup.locator('a[data-back]').click();
  await popup.waitForURL(base+printer);
  await popup.close();
  console.log('PASS direct links and new tabs use a local fallback');

  const routes = ['/', '/dashboard?page=2', printer, printer+'/weekly', printer+'/weekly/history', printer+'/reactive/start',printer+'/reactive/manual',printer+'/reactive/summary',printer+'/diagnosis',printer+'/history','/sops/nozzle-clog?printer=core-one-01','/settings','/testing'];
  for(const route of routes){
    await go(route);
    if(route==='/settings')await page.getByLabel('Farm name',{exact:true}).waitFor();
    if(route==='/testing')await page.getByRole('button',{name:'Run local checks',exact:true}).waitFor();
    assert.equal(await page.locator('a[data-home]').count(),1,route);
    assert.equal(await page.locator('a[data-home]').getAttribute('href'),'/',route);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > 800 || [...document.querySelectorAll('.kiosk-header a,.kiosk-header button')].some(n => n.getBoundingClientRect().right>800));
    assert.equal(overflow,false,route);
  }
  await go(printer);
  const colours=await page.locator('.detail-layout > section').evaluateAll(nodes=>nodes.map(n=>getComputedStyle(n).backgroundColor));
  assert.equal(new Set(colours).size,4);
  assert.deepEqual(errors,[]);
  console.log('PASS all Home controls and header layout at 800x480; four distinct panel colours; no browser errors');
})().catch(error => {console.error(error);process.exitCode=1;}).finally(async () => {
  if(browser)await browser.close();
  server.kill('SIGTERM');
});
