// Mock of the bank's services for the final case, as a Cloudflare Worker.
// Every answer comes from data.js, which make_data.py writes from the case folder's test-customers.json.
// Paths, fields and error answers follow the bank's service contract (sections 3 to 6).
import DATA from "./data.js";

const CORS = { "Access-Control-Allow-Origin": "*" };
const ORIGIN = "ivr-gateway-test:ivr-channel-v0";
const NOT_FOUND = "com.freyabank.ivrgateway.ivrchannel.rest.exception.NotFoundException";
const INVALID = [400, "INVALID_ARGUMENTS_ERROR", "ERROR", "IllegalArgumentException"];

// How each service finds its answer, and what it answers when the customer, card or week is not in the data.
const SERVICES = {
  "cards.list": { by: "customer", need: ["customerNo"],
    missing: [404, "FB_IVR_CHANNEL_102", "WARNING", NOT_FOUND, "Müşteriye ait geçerli kart bilgisi bulunamadı."] },
  "cards.balance": { by: "card", need: ["cardNumber"],
    missing: [404, "FB_IVR_CHANNEL_120", "WARNING", NOT_FOUND, "Kart bakiye servisi hatası: kart bulunamadı"] },
  "statement.week.get": { by: "card", need: ["cardNumber"],
    missing: [404, "FB_IVR_CHANNEL_121", "WARNING", NOT_FOUND, "Ekstre haftası servisi hatası: kart bulunamadı"] },
  "statement.week.decide": { by: "card", week: true, need: ["cardNumber", "newStatementWeek"],
    missing: [...INVALID, "invalid arguments: no preview for this card and week"] },
  "statement.week.change": { by: "card", week: true, need: ["cardNumber", "newStatementWeek"],
    missing: [...INVALID, "invalid arguments: no change for this card and week"] },
  "contact.email.get": { by: "customer", need: ["customerNo", "intAddressInfoType"],
    missing: [...INVALID, "invalid arguments: unknown customer"] },
  "statement.pdf.send": { by: "customer", need: ["customerNo"],
    missing: [404, "FB_IVR_CHANNEL_107", "WARNING", NOT_FOUND, "Pdf dokümanı bulunamadı"] },
};
const FIELD_WORDS = { cardNumber: "card number", customerNo: "customer number" };

const BY_CUSTOMER = {};
const BY_CARD = {};
for (const customer of DATA.customers) {
  BY_CUSTOMER[String(customer.customerNo)] = customer.services;
  for (const [service, entries] of Object.entries(customer.services)) {
    if (SERVICES[service] && SERVICES[service].by === "card") {
      for (const [card, entry] of Object.entries(entries)) {
        (BY_CARD[card] = BY_CARD[card] || {})[service] = entry;
      }
    }
  }
}

function json(body, status = 200) {
  return Response.json(body, { status, headers: CORS });
}

function failure([status, code, type, rootCause, message]) {
  const guid = crypto.randomUUID().replace(/-/g, "");
  return json({ result: { guid, returnCode: code, messageText: message, code, message, type,
    details: { guid, rootCause, errorOrigin: ORIGIN } } }, status);
}

function text(value) {
  return value === undefined || value === null ? "" : String(value).trim();
}

async function serve(name, request) {
  const service = SERVICES[name];
  let body = null;
  try { body = await request.json(); } catch { body = null; }
  const data = body && typeof body.data === "object" && body.data !== null ? body.data : (body || {});
  for (const field of service.need) {
    if (!text(data[field])) return failure([...INVALID, (FIELD_WORDS[field] || field) + " cannot be empty"]);
  }
  let entry = service.by === "customer"
    ? (BY_CUSTOMER[text(data.customerNo)] || {})[name]
    : (BY_CARD[text(data.cardNumber)] || {})[name];
  if (entry && service.week) entry = entry[text(data.newStatementWeek)];
  if (!entry || entry.http === undefined) return failure(service.missing);
  if (entry.delaySeconds) await new Promise((done) => setTimeout(done, entry.delaySeconds * 1000));
  return json(entry.response, entry.http);
}

export default {
  async fetch(request) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: { ...CORS, "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Allow-Headers": "*" } });
    }
    const last = url.pathname.replace(/\/+$/, "").split("/").pop();
    if (request.method === "GET" && last === "health") return json({ status: "ok", customers: DATA.customers.length });
    if (request.method === "POST" && url.pathname.endsWith("/oauth2/token")) {
      return json({ access_token: "test-token", token_type: "Bearer", expires_in: 1800 });
    }
    if (request.method === "POST" && SERVICES[last]) return serve(last, request);
    return json({ error: "not found", services: Object.keys(SERVICES) }, 404);
  },
};
