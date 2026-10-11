# Skeleton reference

## What the agent sees

Dialogue prompt in every turn: the system prompt (your `system_rules` plus fixed tool-use mechanics), then the step's fixed mechanics, then `RULES FOR THIS PART OF THE CALL` (your items), then the fact blocks.

Fact blocks, written by code at run time:

- `SECURITY QUESTIONS` (contact): the questions from the IR, in order, with the field value for each.
- `TODAY` and `CALLBACK WINDOW` (contact): today's date and time, and the exact days and hours a callback may be booked on when the brief has callbacks.
- `CALENDAR` (negotiate): every day from today to past the longest deadline, each marked business day or CLOSED with the next business day. `WEEKDAY WORDS` when the brief defines "this" and "next" weekday.
- `ACCOUNT FACTS` (negotiate): per product the id, the spoken label, the past-due amount, days late, the floor, the deadline, the `DATES THAT CAN BE RECORDED` (every allowed date up to the deadline, so a date is allowed exactly when it is in that list), the persuasion attempts allowed and the amount and date to propose in each attempt.

Fixed mechanics already tell the agent: speak one or two short sentences per turn; call the tool when a rule says to; call one tool per message; obey `what_to_do_next`; never claim something a tool result did not confirm; never invent; say goodbye and call `close_call` in the same message at the end; in contact, never state account information and call `verify_identity` for every complete answer.

## What code enforces (the call is not carried out, and the agent is told why)

- `verify_identity`: incomplete or unreadable answers are not sent; no checks after the check failed or passed; the question order.
- `create_promise`: unknown product, second promise on a product, amount below the floor or above the full amount, date in the past, not a business day, after the product's deadline, more tries than allowed after a tool failure.
- `schedule_callback`: day not allowed, time outside the window, not later than now.
- `add_note`: reason not allowed, product already promised or noted, note on a transferred call when the brief forbids it.
- `close_call`: the outcome and reason are set by code when state decides them (transfer, recorded promise, booked callback, failed identity, note reasons); a pair outside the table is refused; a missing required note is asked for first.

## What only your text can carry

No code hears the conversation. These depend on your items alone: who counts as the right person, how often to ask again, what a third party may be told, refusals of the identity check, what to say when stating the debt, which offer comes when, how refusals and attempts are counted, sympathy, read-back and waiting for a clear yes, re-reading after a change, what to answer to side questions, and the closing pair whenever state does not decide it.

## Guidance keys

The runtime sentence comes first; your text follows it. Placeholders in single braces are replaced.

| Key | Shown when | Your text should say |
|---|---|---|
| `id_correct_next` | an answer was right and questions remain. The next question is appended | usually `""` |
| `id_all_verified` | the whole check passed. The call continues by itself | usually `""` |
| `id_wrong_retry` | an answer was wrong and tries remain. `{left}` | what the brief says about asking again |
| `id_failed` | the wrong-answer limit was reached. Closing values are appended | what the agent must and must not tell the customer |
| `id_unreadable` | the bank could not read the answer; it did not count | how to ask again |
| `id_unreadable_date` | the answer was not a complete date; nothing was sent | how to ask again |
| `id_incomplete` | digits were missing; nothing was sent | usually `""` |
| `promise_ok` | a promise was recorded. Remaining notes or closing values are appended | what must be said after a recorded promise, with any prescribed sentence |
| `promise_fail_retry` | recording failed and a retry is allowed | usually `""` |
| `promise_fail_final` | recording failed for the last allowed time. Closing or note instructions are appended | what to tell the customer, and which note reason applies if notes are used |
| `promise_duplicate` | a promise for that product already exists | what the brief says about changing a recorded promise |
| `amount_below_floor` | the amount was under the floor. `{amount}` `{floor}` `{full}` `{product}` | what to say and offer |
| `date_after_deadline` | the date was after the deadline. `{date}` `{deadline}` `{product}` | what to say and offer, and what a refusal of it means |
| `date_not_business_day` | the date was a closed day inside the deadline. `{date}` `{next_business_day}` | what to offer |
| `callback_ok` | the callback was booked. Closing values are appended | what to say |
| `callback_outside_window` | day or time not allowed. The window is appended | what the brief says about asking for another time and when to stop |
| `transfer_ok` | the hand-over was registered. Closing values are appended | what to say; whether notes are skipped |
| `notes_pending` | products without promise and note remain | when to write the notes and with which reasons |
| `account_unavailable` | the account could not be loaded | what to tell the customer and how to close, using a pair from the table |
| `farewell` | before every close instruction | the brief's farewell rule as an instruction, for example "Thank the customer and say goodbye," |
| `close_choose` | state does not decide the outcome | usually `""` |
| `tool_error_retry` | a tool other than create_promise failed | usually `""` |

## Platform facts behind these rules (from the starter kit documents)

- The routing model never sees the dialogue prompt, so text cannot move the call. This skeleton routes only on tool results.
- A tool is callable only in the step that lists it.
- The agent model sometimes stays silent after a bare tool result or ignores a negative one; a `what_to_do_next` sentence inside the result fixes both.
- A tool description that only describes ("Looks up X") lets the agent acknowledge in words and not call; a trigger ("CALL it when") does not.
- A value in double curly braces is replaced by the platform before the agent sees the text. Your text must not contain double curly braces.
