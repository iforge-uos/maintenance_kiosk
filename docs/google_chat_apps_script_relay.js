const WEBHOOK_SECRET_PROPERTY = "WEBHOOK_SECRET";
const CHAT_WEBHOOK_URL_PROPERTY = "GOOGLE_CHAT_WEBHOOK_URL";

function doPost(event) {
  try {
    const payload = JSON.parse(event.postData.contents || "{}");
    const expectedSecret = PropertiesService.getScriptProperties().getProperty(WEBHOOK_SECRET_PROPERTY);
    const chatWebhookUrl = PropertiesService.getScriptProperties().getProperty(CHAT_WEBHOOK_URL_PROPERTY);

    if (!expectedSecret) {
      return jsonResponse({ ok: false, error: "Missing WEBHOOK_SECRET script property" });
    }
    if (!chatWebhookUrl) {
      return jsonResponse({ ok: false, error: "Missing GOOGLE_CHAT_WEBHOOK_URL script property" });
    }
    if (payload.secret !== expectedSecret) {
      return jsonResponse({ ok: false, error: "Invalid secret" });
    }
    if (!payload.text) {
      return jsonResponse({ ok: false, error: "Missing text" });
    }

    const response = UrlFetchApp.fetch(chatWebhookUrl, {
      method: "post",
      contentType: "application/json; charset=utf-8",
      payload: JSON.stringify({ text: payload.text }),
      muteHttpExceptions: true,
    });

    const status = response.getResponseCode();
    if (status < 200 || status >= 300) {
      return jsonResponse(
        {
          ok: false,
          error: "Google Chat webhook failed",
          status: status,
          body: response.getContentText(),
        }
      );
    }

    return jsonResponse({ ok: true });
  } catch (error) {
    return jsonResponse({ ok: false, error: String(error) });
  }
}

function jsonResponse(body) {
  const output = ContentService.createTextOutput(JSON.stringify(body));
  output.setMimeType(ContentService.MimeType.JSON);
  return output;
}
