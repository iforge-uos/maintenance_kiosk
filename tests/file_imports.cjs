// Run: node --test tests/file_imports.cjs. No credentials or external services.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const imports = require('../kiosk/static/file-imports.js');
const client = {web:{client_id:'test-client.apps.googleusercontent.com', client_secret:'private-test-client-secret',
  auth_uri:'https://accounts.google.com/o/oauth2/auth', token_uri:'https://oauth2.googleapis.com/token'}};
const webhook = 'https://discord.com/api/webhooks/123456789012345678/private-test-webhook-token';
const workbook = 'test_spreadsheet_1234567890';

test('Google downloads retain their fields and accept a byte order mark', () => {
  assert.deepEqual(JSON.parse(imports.parse('google_oauth_client', '\ufeff\n'+JSON.stringify(client))), client);
  const installed = {installed:client.web};
  assert.deepEqual(JSON.parse(imports.parse('google_oauth_client', JSON.stringify(installed))), installed);
  const service = {type:'service_account',client_email:'test@example.invalid',private_key:'test-key',token_uri:client.web.token_uri};
  assert.deepEqual(JSON.parse(imports.parse('google_service_account', JSON.stringify(service))), service);
  const token = {client_id:'test-client',client_secret:'test-secret',refresh_token:'test-refresh'};
  assert.deepEqual(JSON.parse(imports.parse('google_oauth_token', JSON.stringify(token))), token);
});

for (const [name, kind, value] of [
  ['malformed JSON', 'google_oauth_client', '{"client_secret":"private-test-client-secret"'],
  ['wrong credential type', 'google_oauth_client', '{"type":"service_account"}'],
  ['two client types', 'google_oauth_client', JSON.stringify({...client,installed:client.web})],
  ['incomplete client', 'google_oauth_client', JSON.stringify({web:{client_id:'test'}})],
  ['untrusted auth URL', 'google_oauth_client', JSON.stringify({web:{...client.web,auth_uri:'https://accounts.google.com.evil.invalid/auth'}})],
  ['auth URL with password', 'google_oauth_client', JSON.stringify({web:{...client.web,auth_uri:'https://user:private-test-client-secret@accounts.google.com/auth'}})],
  ['untrusted token URL', 'google_oauth_client', JSON.stringify({web:{...client.web,token_uri:'https://example.invalid/token'}})],
  ['incomplete service account', 'google_service_account', '{"type":"service_account"}'],
  ['wrong authorized-user file', 'google_oauth_token', JSON.stringify(client)],
  ['missing refresh token', 'google_oauth_token', '{"client_id":"test","client_secret":"private-test-client-secret"}'],
  ['empty file', 'spreadsheet_id', ' \n'],
  ['binary file', 'spreadsheet_id', 'abc\x00def'],
  ['short invalid ID', 'spreadsheet_id', 'abc'],
  ['multiple IDs', 'spreadsheet_id', workbook+'\n'+workbook],
  ['ambiguous ID JSON', 'spreadsheet_id', JSON.stringify({spreadsheet_id:workbook,url:workbook})],
  ['non-Google URL', 'spreadsheet_id', 'https://example.invalid/spreadsheets/d/'+workbook+'/edit'],
  ['published URL', 'spreadsheet_id', 'https://docs.google.com/spreadsheets/d/e/published-version/pubhtml'],
  ['wrong value type', 'spreadsheet_id', '{"spreadsheet_id":1234567890123}'],
  ['multiple webhook URLs', 'discord_webhook', webhook+'\n'+webhook],
  ['Discord message URL', 'discord_webhook', webhook+'/messages/123456'],
  ['untrusted webhook host', 'discord_webhook', webhook.replace('discord.com','discord.com.evil.invalid')],
  ['webhook plain HTTP', 'discord_webhook', webhook.replace('https:','http:')],
  ['bad query', 'discord_webhook', webhook+'?redirect=https://example.invalid'],
  ['non-numeric thread', 'discord_webhook', webhook+'?thread_id=bad'],
  ['repeated thread', 'discord_webhook', webhook+'?thread_id=123&thread_id=456'],
  ['oversize file', 'discord_webhook', 'private-test-webhook-token'.repeat(4000)],
]) {
  test(`rejects ${name} without exposing file values`, () => {
    assert.throws(() => imports.parse(kind, value), error => {
      assert(!error.message.includes('private-test-'));
      assert(!error.message.includes(value));
      return true;
    });
  });
}

test('spreadsheet text, full URLs, JSON and named values select only the ID', () => {
  for (const value of [workbook, '\ufeff'+workbook+'\r\n',
    `https://docs.google.com/spreadsheets/d/${workbook}/edit?gid=123#gid=123`,
    `https://docs.google.com/spreadsheets/u/0/d/${workbook}/edit`,
    JSON.stringify({spreadsheet_id:workbook}), JSON.stringify({spreadsheetId:workbook}),
    JSON.stringify({url:`https://docs.google.com/spreadsheets/d/${workbook}/edit`}),
    `# This is the workbook\r\nGOOGLE_SPREADSHEET_ID="${workbook}"\r\n`,
  ]) assert.equal(imports.parse('spreadsheet_id', value), workbook);
});

test('Discord text and JSON support one webhook, including valid query parameters', () => {
  for (const value of [webhook, '\ufeff'+webhook+'\n', JSON.stringify({webhook_url:webhook}),
    JSON.stringify({discord_webhook_url:webhook}), `DISCORD_WEBHOOK_URL=${webhook}`,
    `# Channel webhook\nwebhook_url: '${webhook}'`,
  ]) assert.equal(imports.parse('discord_webhook', value), webhook);
  assert.equal(imports.parse('discord_webhook', webhook+'?wait=true&thread_id=123456'), webhook+'?wait=true&thread_id=123456');
  assert.equal(imports.parse('discord_webhook', webhook.replace('/api/','/api/v10/')), webhook.replace('/api/','/api/v10/'));
});

test('file read is bounded and reports UTF-8 errors without raw content', async () => {
  let reads = 0;
  await assert.rejects(imports.readFile({size:65537,arrayBuffer:()=>{reads++;}}, 'spreadsheet_id'), /64 KB/);
  assert.equal(reads, 0);
  await assert.rejects(imports.readFile({size:2,arrayBuffer:async()=>new Uint8Array([0xff,0xfe]).buffer}, 'spreadsheet_id'), /UTF-8/);
  const bytes = new TextEncoder().encode('\ufeff'+workbook);
  assert.equal(await imports.readFile({size:bytes.length,arrayBuffer:async()=>bytes.buffer}, 'spreadsheet_id'), workbook);
  await assert.rejects(imports.readFile({size:1,arrayBuffer:async()=>{throw new Error('private-test-value');}}, 'discord_webhook'), error => !error.message.includes('private-test-value'));
});
