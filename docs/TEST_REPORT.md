# Test report

What was run, what it showed, and what it does not show. Updated 2026-10-11 16:45 UTC. A target, one passing sample, an infrastructure failure and a reproducible pass are kept apart.

## Summary

| Layer | Status | Result |
|---|---|---|
| Local unit and runtime tests | run | 54 of 54 pass |
| Same runtime scenarios with the IR the builder extracted for Brief A+B | run | 35 of 35 pass |
| Offline builds (all model roles, compile, local validation) | run | public A, public B (A as base), holdout clean, holdout raw: all completed |
| Holdout checks, invented bank, clean brief | run | 26 of 26 (first run 25 of 26, see below) |
| Holdout checks, invented bank, raw material | run | 27 of 27 (first run 26 of 27, see below) |
| Platform probes | run | see `docs/PLATFORM_PROBES.md` |
| Publish, readback, export on Freya | run | works; readback shows no difference; 505 KB workflow accepted |
| Fresh end-to-end build with the official two-argument command | run once (public B, 13:05 UTC) | completed in 364 s: 4 model calls, no repair, both verifier passes, dry run, publish, readback without differences, chat smoke, export. The base brief was found by the builder |
| Brief A, 10 practice cases, three runs in a row | run on two builds | build of 14:22 UTC: 7 of 10 customers passed 3 of 3. Build of 14:40 UTC: 10 of 10 customers passed 3 of 3 (30 of 30 calls) |
| Brief B, 3 practice cases, three runs in a row | run on two builds | build of 14:22 UTC: 3 of 3 customers passed 3 of 3 (9 of 9 calls). Build of 14:41 UTC: 1 of 3, because the agent sent several promise calls in one message (see "Several tool calls in one message"); fixed afterwards |
| Build of 15:43 UTC (source `d349f3b`), three runs in a row | run | Brief A: 10 of 10 customers at 3 of 3 (30 of 30 calls). Brief B: 2 of 3 customers (PUB-12 failed 0 of 3: two proposals to a product that allows one) |
| Same model output, account facts of `3aab1ca`, three runs in a row | run | Brief B: 2 of 3 customers (8 of 9 calls; PUB-12 2 of 3, one call with a note written twice). Brief A: 9 of 10 customers (29 of 30 calls; PUB-05 2 of 3, the support team offered twice in one call) |
| Frozen source, three runs in a row, same model output (build of 15:43 UTC) | run | Brief A at `dffffab`: 10 of 10 customers at 3 of 3 (30 of 30 calls). Brief B at `c81fd2e`: 3 of 3 customers at 3 of 3 (9 of 9 calls). 13 of 13 in total; the two commits differ only in note handling, which Brief A's workflow does not contain |
| Run of 15:30 UTC | void | 0 of 13: the platform's agent model answered no second turn at all, also for an old unrelated workflow. Infrastructure, not a result |
| Talking styles, chat route with a model-played customer | run once on Brief A | corrects: 10 of 10 bank records right. pieces: 9 of 10. talkative: 6 measured, 4 stopped by platform timeouts. Two real findings, both fixed afterwards. Other styles and Brief B: NOT RUN |
| All simulations of the day | 23 runs, 152 calls until 15:15 UTC | 128 calls passed by the kit's rule (84 percent). Every one of the 13 customers passed at least once |

The three-runs-in-a-row figures are the ones the event scores by. They differ between builds made from almost the same source: every fresh build writes the prompt text again, and the phone model reacts to small differences in wording. The fixes of the afternoon move behaviour from that text into fixed text and code for this reason. What is NOT known: how the final source scores, because the platform stopped answering before it could be measured.

## Local tests (`python -m unittest discover -s tests`)

`tests/test_runtime.py` compiles a workflow and runs the exact generated hook and code-node snippets in a local model of the engine, against a fake bank that follows the tool API and enforces nothing from the brief.

- 13 public cases: a scripted caller makes the tool calls a correct agent would make, including the wrong first attempts the cases describe (date after the deadline, holiday, amount under the floor, wrong security answer, tool failure). The resulting ledger is scored with the kit's own `score_ledger`. Brief A cases use the Brief A policy (no notes), Brief B cases the A+B policy. Ledgers are scored untouched.
- Each case runs in 12 configurations: two ways of stopping a call (`blank_url`, `redirect_read`) times six engine assumptions (whether pre-hook variable writes persist, whether tool arguments are available as flattened variables, whether the sandbox splits globals and locals).
- Guards: incomplete digits and unreadable dates are not sent; leading zeros survive; no check after a failed check; duplicate promise; weekend, past and holiday dates; retry limit after tool failures; callback window; closing pair outside the table; an outcome that claims an event that did not happen; a promise on a product that already has a note; no note after a transfer when the brief forbids it; the close guard counts only its own refusals; no account read before verification in `redirect_read` mode.
- Honesty when a guard cannot stop a call: with an engine that ignores what a pre-hook does to the request, the invalid promise reaches the bank and the agent is told it IS recorded, because that is what happened.
- Calendar: both public deadlines, the Friday roll-over of the attempt 1 date, a holiday inside the count, the weekday-word table.

What this proves: the hook logic, the state handling, the calendar and amount arithmetic, and that the code runs as generated. What it does not prove: that the phone model follows the guidance, and that the platform behaves like the local engine model.

## Offline builds

| Build | Seconds | Model calls | Repairs | Verifier |
|---|---|---|---|---|
| public A | 402 | 6 | IR 1 | IR pass in round 2; prompts pass, 3 minor issues left |
| public B on A | 400 | 6 | prompts 1 | IR pass in round 1; prompts pass in round 2 |
| holdout clean, first build | about 330 | 4 | none | pass, pass |
| holdout raw, first build | about 340 | 4 | none | pass, pass |
| holdout clean, second build from the final source | 248 | 4 | none | pass, pass |
| holdout raw, second build from the final source | 302 | 6 | prompts 1 | pass; prompts pass in round 2 |

Both holdouts were built twice, the second time after all fixes, and the hand-written checks pass on both builds (26 of 26 and 27 of 27). Model output differs between builds, so this is two samples, not a guarantee.

Build time is well inside the 30 minute limit in every run so far. These runs stop before any Freya call; a full run adds dry run, publish, readback, one chat turn and export.

The IR extracted for public A was compared by hand with the hand-written fixture: identity questions, calendar, bands, deadlines, persuasion offers, promise limits and callback window agree. The A+B IR drives all 13 scripted public cases to a passing ledger (second row of the summary).

## Holdouts (`python tools/run_holdout.py clean|raw`)

Two invented banks with rules and tool APIs that share nothing with the public briefs: other identity fields (postcode and birth year; company number and founding year), other field names and enums in every tool, another success flag (`status: "ok"`), a six-day business week, deadlines in calendar days with a backward adjustment, a percentage floor, three tries for the promise tool, no callback in one and no notes in the other, and for the raw one a mail, a policy extract, a memo that replaces parts of it, a correcting mail and a code list with a retired code.

The expectations were written by hand from the briefs before the builds. The fake bank uses hand-written bindings, not the builder's IR, so a wrong binding would show.

- Clean: 25 of 26 on the first run. The failing check ("closing waits for the note of the other account") was a real defect in the runtime: the close guard gave up after two refusals of any tool. Fixed (a close-specific counter) and covered by a unit test; 26 of 26 after the fix.
- Raw: 26 of 27 on the first run. The failing check was an error in the hand-written expectation (the code list has 12 pairs, the check said 11). The builder was right; the expectation was corrected.
- The memo overrides were resolved correctly: the extra closed day, the shorter deadline and the removed further proposal for products over 60 days, the corrected callback end time, the retired reason left out of the closing table.

What this shows: the analyst, the compiler and the runtime handle another bank's rules and API without any change to the builder. What it does not show: no bank customers exist for these briefs, so nothing was simulated, and two holdouts do not cover every rule kind a hidden brief may contain. Rules the typed IR cannot express are listed per run in the trace (`UNMODEL` lines) and are carried by prompt text only.

## Practice runs on Freya

| When (UTC) | Agent | Cases | Result | Notes |
|---|---|---|---|---|
| 09:28 | public A, IR and prompts from the 08:41 offline build | PUB-01, PUB-02 | 2 of 2 | first live run of the workflow; slowest turn 2 s |
| 09:29 | same agent | Brief A, 10 cases | 9 of 10 | median slowest turn 9 s, worst 18 s, no hang-up |
| 09:36 | fresh builds of A and B | none | infrastructure failure | both builds finished their model stages, then `POST /api/v2/workflow?dryRun=true` returned 504; `GET /api/v2/workspaces` returned 504 three times in a row at 09:48. Nothing was published, so nothing was measured |
| 10:07 | public A, model output of the 09:37 build, runtime with the dates list | Brief A, 10 cases | 9 of 10 | the runner lost the simulation on a 504 while polling; it finished on the platform and was scored afterwards with `tools/score_simulation.py` (kit ledger scoring plus the stored judge verdicts). PUB-07 passes. PUB-06 fails: infrastructure, see below |
| 10:40 | same agent | Brief A, 10 cases | not scored | every call got a verdict (6 pass, 4 inconclusive, two of the inconclusive ones with the reason "all criteria met"), but the simulation never left `running` and its calls could not be read reliably. It then blocked the queue: 35 minutes of "another simulation is running" from 12:06 |
| 11:40 | public B, first publish | none | infrastructure failure | the create request returned 504 after a minute. The build failed; the create step is now sent again (at most twice) |
| 11:45 | public B, model output of the 09:37 build | none | published | five 504 answers on the way, then created, published, readback without differences, chat smoke pass. The simulation could not be created (504) |
| 12:45 | public B | PUB-11, chat route | bank record as expected | not the official measure, see below |
| 12:57 | public B, runtime with the clearer after-promise guidance | PUB-11, PUB-12, PUB-13, chat route | PUB-13 as expected; PUB-11 and PUB-12 infrastructure | agent turns took up to 59 s; the chat route answers 504 after 60 s |
| 13:04 | same agent | PUB-11, PUB-12, chat route | infrastructure, both | PUB-11 stopped by a 504 in the turn that records the promises, two of three recorded; PUB-12 stopped by a 504 right after the account read |
| 13:12 | public B, fresh build of 13:05 | PUB-11, PUB-12, PUB-13, chat route | PUB-11 and PUB-12 infrastructure; PUB-13 FAIL | PUB-13: the bank answered the second `create_promise` with a gateway error page; see "A bank request without an answer" |

The one failure, PUB-07 (class: conversation). The customer named Monday 26 October, a bank holiday inside the deadline. The agent did not look the day up; it treated it as too late and offered the deadline, 2 November, which the customer accepted. The ledger therefore had 2 November where 27 October was expected. No guard could catch it: 2 November is a valid date and the agent never tried to record the 26th. Change made: every product now lists its DATES THAT CAN BE RECORDED, with a one-line reading rule (missing from the list but before its last date means closed day, later than its last date means after the deadline), and the developer skill tells the prompt writer to make the agent look a named date up before answering. Not yet re-measured because of the outage.

In the nine passing calls the tool sequences matched the expected ones, including PUB-10 (promise service fails once: two `create_promise` calls, one recorded) and PUB-03 (three wrong answers, closed as `not_verified` / `failed` with no account read).

PUB-07 after the change (10:07 run): the agent said "Monday 26 October is a closed day. The next business day is Tuesday 27 October", the customer agreed, and 27 October was recorded. One passing run; the change is confirmed for this conversation, not proven reliable.

PUB-06 in the 10:07 run (class: infrastructure). The conversation was correct up to the confirmed read-back and the promise was recorded with the right amount and date. The agent's turns took 29, 33 and 52 seconds; the customer ended the call during the last one, before `close_call`. The same case passed at 09:29 with turns of a few seconds. In this run the whole simulation took more than 12 minutes against 6 in the earlier one.

Chat-route runs (`tools/chat_cases.py`). The customer is played by a model through the `claude` CLI with the kit's persona text, the bank record is scored with the kit's ledger scoring, and nobody judges the conversation checks; the transcript is read against them by hand. The chat route may skip the first code step; the runtime computes those values on the first hook instead. What these runs show for Brief B:

- PUB-11: two verifications, account read, three `create_promise` calls, closed as `promise`. All three products were stated before any ask; one read-back and one confirmation covered all three. Weakness seen: the read-back said the dates as `2026-10-16` instead of in words.
- PUB-13: the credit card was recorded on 30 October and the loan on its own deadline, 23 October; the agent told the customer the loan's latest date before the read-back, and the read-back named both dates.
- PUB-12: up to the point where the chat turn timed out, the phone question was asked again once and the date of birth was not. The part the case is about (one promise, two `refused` notes, two attempts and one attempt) was never reached. NOT MEASURED.
- Change made after the first PUB-11 transcript: after the first of several promises the guidance listed the other products as "without a promise and without a note" and went on to the note instruction, and the agent spoke after every promise (the text-message sentence three times, one 47 second turn). The guidance now puts recording the confirmed promises first and speaking once after the last. In the PUB-13 run after the change the agent recorded both and spoke once.

A bank request without an answer (PUB-13, 13:12 run; class: infrastructure that exposed a runtime gap). The promise for the credit card was recorded. The request for the loan came back from the bank's host as an HTML error page. On Freya that is a bare tool error: no post-hook runs, so the runtime never learned of it and gave no guidance. The agent told the customer the loan promise could not be recorded and wrote a `service_error` note instead of trying again, so the record had one promise and one note where two promises were expected. Changes made, all general: the pre-hook of the next call sees that the previous `create_promise` never got its answer and counts it as a failed try; a note for a product whose promise failed while a try is left is refused once with the instruction to call `create_promise` again; the fixed part of the system prompt says that a bare tool error means nothing was done and the tool is called again once. Two local tests cover it (one lost request then success, two lost requests then the note). Not re-measured on Freya: the failure cannot be provoked on purpose.

The stored events of a PUB-10 practice call then showed that the scripted failure of that case arrives the same way: the bank answers with an HTTP error status, the agent reads `API request failed: {'ok': False, ...}`, and no post-hook runs. So on the platform the runtime had never counted a failed promise; the retry in every passing PUB-10 run came from the prompt text alone, and a case with two failures would have left the closing pair to the model. With the change above the count is kept in code (the next pre-hook, including the one of `close_call`). The local engine now models this behaviour, and the tests that had assumed guidance after a failed request were corrected to it.

10:27 simulation, scored after the fact at 13:33 while it was still `running`: 5 of 10 by the kit's rule, which counts an unfinished analysis as a failure. Bank records were as expected in 9 of 10. Four calls had no final verdict (`inconclusive`), two of them with the reason "all criteria met"; for PUB-10 the judge objected to the word "recorded" in the opening line, which the brief prescribes. PUB-07 ended without `close_call` after the promise was recorded (the customer ended the call).

In the same run the model that played the customer (not the agent) gave a wrong date of birth taken from the persona's example sentence and called 12 October "tomorrow" although the call's today is 14 October. The agent refused the past date correctly. These are artefacts of the chat tool's customer, one more reason not to read chat-route runs as scores.

## Three runs in a row (15:00 to 18:40 Istanbul time)

| Build (UTC) | Source | Brief A, customers at 3 of 3 | Brief B, customers at 3 of 3 | What failed |
|---|---|---|---|---|
| 14:22 | `bb77fc7` | 7 of 10 (runs: 7, 8, 9) | 3 of 3 (runs: 3, 3, 3) | PUB-03: a third wrong identity answer that came with a question was not submitted, the customer got a fourth try. PUB-05: payment proposals after a job-loss mention. PUB-06: the lowest amount offered more than once; once a platform sandbox failure |
| 14:40 (A), 14:41 (B) | `a2cad20`, `9612fd7` | 10 of 10 (runs: 10, 10, 10) | 1 of 3 (runs: 2, 1, 2) | Brief B: several promise calls in one message, see below |
| 15:18 | `d5ab219` | void | void | the platform's agent model did not answer; every call ended after the greeting |
| 15:43 | `d349f3b` | 10 of 10 (runs: 10, 10, 10) | 2 of 3 (runs: 2, 2, 2) | PUB-12, all three runs: the product that allows one persuasion attempt got two proposals. The account facts gave the two variants of the first offer as two sentences, and the agent used both |
| 15:43, republished with the account facts of `3aab1ca` | same model output | 9 of 10 (runs: 10, 9, 10) | 2 of 3 (runs: 3, 2, 3) | PUB-12 once: two notes and the closing call in one message, a note written twice (see below). PUB-05 once: the support team offered twice |

Agent mistakes found in these runs and what was changed, each as a general rule and none for a single case:

- Identity answer with a question attached: fixed text, "CALL verify_identity with the answer first".
- A rule that switches other rules off (hardship): developer skill, the exception is written into the rule it switches off; an offer is written as made once with what follows a no; fixed text, a declined offer is over.
- Conditional first offer: the account facts give both variants as two sentences.
- The word "recorded" in the opening sentence, flagged by the judge as said before a successful call in three of six runs of PUB-10: analyst skill, the first sentence avoids a word the brief reserves for later unless the brief quotes the sentence. PUB-10 passed 6 of 6 afterwards.

### Several tool calls in one message

In the 14:41 build of Brief B the agent sent the promises for all products in one message, and Freya ran them at the same time. Earlier builds had always sent them one after another. The runtime links a call's pre-hook to its post-hook through one variable; with three calls at once it was overwritten, a recorded promise was attributed to the wrong product, the agent tried the "missing" one again, the bank refused it as a duplicate, and the runtime counted a service failure and asked for a failure note. Bank records then had all promises and one or two notes that should not exist.

Changes: a bank refusal saying the product already has a promise marks it as recorded (the bank is right, whatever the runtime believed); a waiting promise for another product is no longer taken as a lost call; the post-hook reads the request of its own call when the platform exposes it; a success clears the failure count; the fixed text asks for one promise call per message. The local engine can run a batch of calls with all pre-hooks first, and the test passes whether or not the post-hook can see its request. NOT re-measured on Freya.

### Calls of one message undo each other's state

In the run above the agent sent two `add_note` calls and `close_call` in one message. Both post-hooks ran at the same time, each saved the whole call state it had loaded, and the note of one product was gone from the state. The agent was told that product was still open and wrote the note again; the bank accepted it, so the record had three notes where two were expected. Change (`dffffab`): a recorded promise or note is also written to a variable per product and merged whenever the state is loaded. The local engine can run post-hooks that all start from the same state, and a test covers the case.

What the fix above did not cover showed in a chat call on that commit: with two notes and the closing call in one message, both post-hooks reported the same product as noted. The post-hook cannot see its own request (the `config` name does not exist there), and each one read the record the last pre-hook had left. So the variable per product was written for one product only. Change (`c81fd2e`): pre-hooks run one after another, and each records that its note was sent; when answers are back and none reported a failure, every sent note counts as written, and a second note for that product is refused before it reaches the bank. After it: Brief B three runs in a row, 9 of 9 calls passed; PUB-12 over the chat route, 6 of 6.

Not fixed: in one PUB-12 call of the run before, the agent made one proposal to the product that allows two. A change to the account facts for it was dropped because Brief A could not be measured again in time.

### Platform sandbox failures

Twice a tool call ended with `Failed to reach snekbox: [Errno 104] Connection reset by peer`: the request had reached the bank, the platform could not run the post-hook. The promise was recorded at the bank and unknown to the runtime. The duplicate rule above covers this too.

### Chat route with a model-played customer and judge (`tools/chat_cases.py`)

Used next to the simulation queue, three calls at a time. The agent is the real one; customer and judge are another model, so these are not scores.

- Before the 14:40 measurement: PUB-03, PUB-05, PUB-06 three times each, 9 of 9 as expected.
- Styles on Brief A (build of 14:40): `corrects` 10 of 10 bank records right; `pieces` 9 of 10; `talkative` 6 measured, 4 stopped by timeouts. Three "fails" of that run were the tool's judge not seeing tool results, corrected in the tool.
- `pieces`, PUB-03: the customer said dates in two parts. The agent joined the first, then asked for "the full date" again and again, so the third wrong answer was never checked. Change: the runtime keeps a date fragment and joins the next one, as it did for digits; fixed text tells the agent to put parts together or submit what it has.
- `talkative`, PUB-05: the agent said it was sorry about the traffic, a second sympathy sentence. Change: fixed text, small talk is not commented on, and the customer's own situation is not small talk.

## Known limits, not yet measured

1. Everything the phone model must do in speech: right-party confirmation, counting refusals and persuasion attempts, read-back and waiting for a clear yes, sympathy sentences. Code supplies the figures and dates but cannot observe speech.
2. Platform availability. The API was unstable from 09:43 UTC onwards. A run waits out transient failures inside its deadline, makes no model call when the preflight fails, and sends an unanswered create again, but an outage longer than the run budget fails the run.
3. A call that ends without `close_call` (customer hangs up, turn limit) leaves the bank record open. There is no fallback close. This is the PUB-06 failure above.
4. Latency. A turn with several tool calls (three promises and the close) is several model calls long. With a healthy platform the slowest turn of a Brief A call was 9 s in the median and 18 s at worst; Brief B's longer turns were only seen while the platform was slow.
5. The frozen source was measured on one set of model output only (13 of 13 customers at 3 of 3). A fresh build writes the prompt text again, and earlier fresh builds from nearly the same source scored between 10 and 12 of 13.
6. Build-to-build variation: two builds from nearly the same source scored 7 and 10 customers of 10 at 3 of 3. One fresh build per hidden brief is one draw.
7. Styles `asks_back` and `in_words`, and all styles on Brief B, were not run.
