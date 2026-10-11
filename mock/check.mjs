// Checks the Worker against every answer in the test customers file, and the answers for unknown keys.
//   node mock/check.mjs <case_folder>                 the Worker code in this folder, in process
//   node mock/check.mjs <case_folder> <mock_url>      a deployed Worker
import fs from "node:fs";
import path from "node:path";
import assert from "node:assert/strict";
import worker from "./worker.js";

const [folder, base] = process.argv.slice(2);
const data = JSON.parse(fs.readFileSync(path.join(folder, "test-environment", "test-customers.json"), "utf8"));
const PREFIX = "/voice/v1/";
let checked = 0;
const failed = [];

async function post(service, body) {
  const init = { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ data: body, call: { ani: "", lang: "tr" } }) };
  const started = Date.now();
  const response = base ? await fetch(base.replace(/\/+$/, "") + PREFIX + service, init)
                        : await worker.fetch(new Request("https://mock.local" + PREFIX + service, init));
  return { status: response.status, body: await response.json(), seconds: (Date.now() - started) / 1000 };
}

async function expectEntry(label, service, body, entry) {
  const got = await post(service, body);
  checked += 1;
  try {
    assert.equal(got.status, entry.http);
    assert.deepEqual(got.body, entry.response);
    if (entry.delaySeconds) assert.ok(got.seconds >= entry.delaySeconds, "answered before the delay");
  } catch (e) {
    failed.push(label + ": " + String(e.message).slice(0, 200));
  }
}

async function expectError(label, service, body, status, code) {
  const got = await post(service, body);
  checked += 1;
  const r = got.body.result || {};
  if (got.status !== status || r.code !== code || r.returnCode !== code || got.body.data !== undefined) {
    failed.push(`${label}: wanted ${status} ${code}, got ${got.status} ${r.code}`);
  }
}

for (const c of data.customers) {
  const s = c.services;
  for (const customerNo of [c.customerNo, Number(c.customerNo)]) {
    await expectEntry(`${c.customerNo} cards.list`, "cards.list", { customerNo, aiFlow: "CardOps" }, s["cards.list"]);
    for (const kind of ["E", "P", "B"]) {
      await expectEntry(`${c.customerNo} contact.email.get ${kind}`, "contact.email.get", { customerNo, intAddressInfoType: kind }, s["contact.email.get"]);
    }
  }
  if (s["statement.pdf.send"]) {
    await expectEntry(`${c.customerNo} statement.pdf.send`, "statement.pdf.send",
      { customerNo: c.customerNo, cardNumber: "x", currencyType: "TL", statementDate: "01.01.2026", emailAddress: "a@b.example" }, s["statement.pdf.send"]);
  } else {
    await expectError(`${c.customerNo} statement.pdf.send (no entry)`, "statement.pdf.send", { customerNo: c.customerNo, cardNumber: "x" }, 404, "FB_IVR_CHANNEL_107");
  }
  for (const service of ["cards.balance", "statement.week.get"]) {
    for (const [card, entry] of Object.entries(s[service] || {})) {
      await expectEntry(`${c.customerNo} ${service} ${card}`, service, { cardNumber: card }, entry);
    }
  }
  for (const service of ["statement.week.decide", "statement.week.change"]) {
    for (const [card, weeks] of Object.entries(s[service] || {})) {
      for (const week of ["1", "2", "3", "4"]) {
        if (weeks[week]) {
          await expectEntry(`${c.customerNo} ${service} ${card} week ${week}`, service, { cardNumber: card, newStatementWeek: week, statementDay: 5 }, weeks[week]);
          await expectEntry(`${c.customerNo} ${service} ${card} week ${week} as number`, service, { cardNumber: card, newStatementWeek: Number(week), statementDay: 5 }, weeks[week]);
        } else {
          await expectError(`${c.customerNo} ${service} ${card} current week ${week}`, service, { cardNumber: card, newStatementWeek: week, statementDay: 5 }, 400, "INVALID_ARGUMENTS_ERROR");
        }
      }
    }
  }
}

await expectError("unknown customer cards.list", "cards.list", { customerNo: "10000000", aiFlow: "CardOps" }, 404, "FB_IVR_CHANNEL_102");
await expectError("unknown card cards.balance", "cards.balance", { cardNumber: "000000******0000" }, 404, "FB_IVR_CHANNEL_120");
await expectError("unknown card statement.week.get", "statement.week.get", { cardNumber: "000000******0000" }, 404, "FB_IVR_CHANNEL_121");
await expectError("unknown card statement.week.decide", "statement.week.decide", { cardNumber: "000000******0000", newStatementWeek: 2, statementDay: 5 }, 400, "INVALID_ARGUMENTS_ERROR");
await expectError("unknown card statement.week.change", "statement.week.change", { cardNumber: "000000******0000", newStatementWeek: 2, statementDay: 5 }, 400, "INVALID_ARGUMENTS_ERROR");
await expectError("unknown customer contact.email.get", "contact.email.get", { customerNo: "10000000", intAddressInfoType: "E" }, 400, "INVALID_ARGUMENTS_ERROR");
await expectError("unknown customer statement.pdf.send", "statement.pdf.send", { customerNo: "10000000", cardNumber: "x" }, 404, "FB_IVR_CHANNEL_107");
await expectError("empty card number", "cards.balance", { cardNumber: "" }, 400, "INVALID_ARGUMENTS_ERROR");
await expectError("empty intAddressInfoType", "contact.email.get", { customerNo: "90000001", intAddressInfoType: "" }, 400, "INVALID_ARGUMENTS_ERROR");

console.log(`${checked - failed.length} of ${checked} answers as in the test customers file` + (base ? ` (${base})` : " (in process)"));
for (const line of failed.slice(0, 30)) console.log("  FAIL " + line);
process.exit(failed.length ? 1 : 0);
