/* Read only a file selected by the operator. Never store credentials in a draft. */
((root, factory) => {
  const imports = factory();
  if (typeof module === 'object' && module.exports) module.exports = imports;
  else root.KioskFileImports = imports;
})(typeof window === 'undefined' ? globalThis : window, () => {
  'use strict';
  const MAX_FILE_BYTES = 64 * 1024;
  const googleTokenUri = 'https://oauth2.googleapis.com/token';
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const textFields = (value, names) => object(value) && names.every(name => typeof value[name] === 'string' && value[name].trim());
  const fail = message => {const error = new Error(message); error.name = 'FileImportError'; throw error;};
  function json(text) {
    try { return JSON.parse(text); }
    catch { fail('This file is not valid JSON. Select the original credential or a valid JSON file.'); }
  }
  function url(value, message) {
    if (/\s|\\/.test(value)) fail(message);
    let parsed;
    try { parsed = new URL(value); } catch { fail(message); }
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.port) fail(message);
    return parsed;
  }
  function googleCredential(kind, text) {
    const value = json(text);
    if (!object(value)) fail('Select a Google credential JSON object.');
    if (kind === 'google_oauth_client') {
      const choices = ['web', 'installed'].filter(key => Object.hasOwn(value, key));
      if (choices.length !== 1) fail('Select the OAuth client JSON downloaded from Google Cloud.');
      const client = value[choices[0]];
      if (!textFields(client, ['client_id', 'client_secret', 'auth_uri', 'token_uri'])) {
        fail('The OAuth client JSON is missing its connection fields.');
      }
      const authorization = url(client.auth_uri, 'Use the original OAuth client JSON with Google HTTPS endpoints.');
      if (authorization.hostname !== 'accounts.google.com' || client.token_uri !== googleTokenUri) {
        fail('Use the original OAuth client JSON with Google HTTPS endpoints.');
      }
    } else if (kind === 'google_service_account') {
      if (value.type !== 'service_account' || !textFields(value, ['client_email', 'private_key', 'token_uri'])) {
        fail('Select a service account JSON key with its email, private key, and token address.');
      }
      if (value.token_uri !== googleTokenUri) fail('Use the original service account JSON with the Google token address.');
    } else {
      if ((value.type !== undefined && value.type !== 'authorized_user') ||
          !textFields(value, ['client_id', 'client_secret', 'refresh_token']) ||
          (value.token_uri !== undefined && value.token_uri !== googleTokenUri)) {
        fail('Select an authorized-user OAuth JSON file with a Google refresh token.');
      }
    }
    return JSON.stringify(value, null, 2);
  }
  function singleValue(text, keys, description) {
    if (/^[\[{\"]/.test(text)) {
      const data = json(text);
      if (typeof data === 'string') return data.trim();
      if (!object(data)) fail(`Select a file with one ${description}.`);
      const names = Object.keys(data).filter(key => keys.includes(key.toLowerCase()));
      if (names.length !== 1 || typeof data[names[0]] !== 'string') fail(`Select a file with one ${description}.`);
      return data[names[0]].trim();
    }
    // Allow one raw value or one named value, plus blank lines and # comments.
    const lines = text.split(/\r?\n/).map(line => line.trim()).filter(line => line && !line.startsWith('#'));
    if (lines.length !== 1) fail(`Select a file with one ${description}. Use a separate file for each value.`);
    const match = lines[0].match(/^([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(.*)$/);
    let value = lines[0];
    if (match && keys.includes(match[1].toLowerCase())) value = match[2].trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) value = value.slice(1, -1).trim();
    return value;
  }
  function spreadsheet(text) {
    let value = singleValue(text, ['spreadsheet_id', 'spreadsheetid', 'google_spreadsheet_id', 'url'], 'spreadsheet ID or URL');
    if (/^https?:\/\//i.test(value)) {
      const parsed = url(value, 'Use the ID or the full HTTPS Google Sheets URL.');
      const match = parsed.pathname.match(/^\/spreadsheets\/(?:u\/\d+\/)?d\/([A-Za-z0-9_-]+)(?:\/(?:edit|view|preview|copy|export|htmlview))?\/?$/);
      if (parsed.hostname !== 'docs.google.com' || !match) fail('Use the spreadsheet edit URL from Google Sheets.');
      value = match[1];
    }
    if (!/^[A-Za-z0-9_-]{10,200}$/.test(value)) fail('The file must contain a valid Google spreadsheet ID or URL.');
    return value;
  }
  function discord(text) {
    const value = singleValue(text, ['webhook_url', 'discord_webhook_url', 'url'], 'Discord webhook URL');
    const message = 'Use a full HTTPS webhook URL from Discord channel settings.';
    const parsed = url(value, message);
    if (!['discord.com', 'discordapp.com', 'canary.discord.com', 'ptb.discord.com'].includes(parsed.hostname) || parsed.hash ||
        !/^\/api(?:\/v\d+)?\/webhooks\/\d{5,30}\/[A-Za-z0-9._-]{10,}\/?$/.test(parsed.pathname)) fail(message);
    if ([...parsed.searchParams.keys()].some(key => !['wait', 'thread_id'].includes(key))) {
      fail('Discord webhook URLs support only wait and thread_id parameters.');
    }
    const threads = parsed.searchParams.getAll('thread_id');
    if (threads.length && (threads.length !== 1 || !/^\d+$/.test(threads[0]))) fail('The Discord thread ID must be numeric.');
    return value;
  }
  function parse(kind, content) {
    if (typeof content !== 'string' || new TextEncoder().encode(content).length > MAX_FILE_BYTES) fail('Select a text file of 64 KB or less.');
    const text = content.trim(); // Also removes a UTF-8 byte order mark.
    if (!text) fail('This file is empty. Select a file that contains the required value.');
    if (/[\x00-\x08\x0b\x0c\x0e-\x1f\ufffd]/.test(text)) fail('Select a UTF-8 text or JSON file.');
    if (['google_oauth_client', 'google_service_account', 'google_oauth_token'].includes(kind)) return googleCredential(kind, text);
    if (kind === 'spreadsheet_id') return spreadsheet(text);
    if (kind === 'discord_webhook') return discord(text);
    fail('This field does not support file import.');
  }
  async function readFile(file, kind) {
    if (!file || !Number.isFinite(file.size) || file.size > MAX_FILE_BYTES) fail('Select a text file of 64 KB or less.');
    let content;
    try {
      const bytes = await file.arrayBuffer();
      content = new TextDecoder('utf-8', {fatal:true}).decode(bytes);
    } catch {
      fail('The file could not be read as UTF-8 text. Select a text or JSON file.');
    }
    return parse(kind, content);
  }
  return Object.freeze({MAX_FILE_BYTES, parse, readFile});
});
