# Policy IR reference

Exact meaning of each field. The compiler and the runtime read these fields literally.

## meta, call_variables

- `meta.brief_kind`: `clean` for one coherent specification, `raw` for collected bank material (mails, extracts, memos, code lists).
- `call_variables.customer_id`, `call_variables.now`: the NAMES of the call variables that hold the customer id and the current date-time. `others`: every other call variable the brief lists (and `now` again), with its meaning and the example value from the brief (`""` if none).

## opening

- `first_line`: the fixed first thing the agent says, including the question that asks for the right person if the brief has one. `{{variable}}` for call variables.
- `forbidden_before_verification`: single words or short phrases the brief forbids before identity is verified (for example a word in quotes in the brief). Only what the brief lists.

## identity

- `questions`: in the order they must be asked. `field` is the value the tool API expects for this question. `ask` is a natural question sentence. `format`: `date` (a calendar date), `digits` (a number where leading zeros matter, `length` = how many digits, 0 if not fixed), `year` (four digits), `text` (anything else; `normalize` lists the clean-up steps the tool API or the brief asks for, usually none).
- `numeric_date_order`: how to read a date given only as numbers (`dmy`, `mdy`, `ymd`), `""` if the brief does not say. `wire_format`: the format the tool expects for a date answer, written with `YYYY`, `MM`, `DD` (for example `YYYY-MM-DD`); `""` for non-dates.
- `ordered`: true when the order matters or the brief gives one.
- `max_wrong_answers`: the number of wrong answers IN TOTAL that ends the check. If the brief counts per question, write it in `unmodeled_rules`.
- `hints`: values the verify tool can return besides right or wrong. `instruction` is what the agent must then do, from the brief. `counts_as_wrong`: false when the brief says that retry does not count.

## calendar

- `weekend_days`: lower-case day names that are not business days. `holidays`: ISO dates. `week_start`: lower-case day name, `monday` unless the brief says otherwise.
- `this_weekday` / `next_weekday`: how the brief defines "this Friday" and "next Friday". `current_week` = the one in the current calendar week. `following_week` = that weekday in the following calendar week. `next_occurrence` = the next such day after today. `unspecified` when the brief gives no definition.

## account

- `product_types`: one entry per product type of the tool API. `label` is how the agent names the product in words; `{field}` inserts a product field from the tool API (for example `card ending {card_last4}`). `floor`: the lowest amount that may be recorded for this type. `floor_label`: what that amount is called in words. If there is no floor, use `{"kind": "fixed", "amount": 0}` and set `promise.min_amount` to `none`.
- A type entry `*` applies to every product type that has no entry of its own.

### AmountExpr

`{kind, field, percent, amount}`. `field`: the value of that product field. `percent_of_field`: `percent` percent of that field. `fixed`: `amount`. `full`: the full past-due amount. Unused members are `""` or `0`.

## bands, persuasion

- `bands`: lateness groups (or any grouping on one numeric product field). A product is in the first band where `min <= value <= max`, both included. Use a very large `max` for "and above". `deadline`: the latest date a promise may have. `persuasion_attempts`: how many times the agent may try again after a refusal.
- If the brief has one rule for all products, write one band with `field: ""`.
- `persuasion`: what the agent proposes in each attempt. `attempt` starts at 1. `bands`: band names it applies to, `[]` for all. `amount`: `full`, `floor`, or `full_or_floor_if_declined` (the full amount, but the floor when the customer already said the full amount is too much). `date`: a DateExpr, or `{"kind": "deadline"}` for the product's deadline.

### DateExpr

`{kind, n, week_offset, roll_if_today, adjust, date}`.

- `today`.
- `business_days_after_today`: the `n`-th business day after today, today not counted. `n: 1` is the next business day.
- `calendar_days_after_today`: today plus `n` days.
- `last_business_day_of_week`: the last business day of the week `week_offset` weeks from the current one (0 = this week). `roll_if_today: true` moves it one week later when that day is today.
- `end_of_month`: the last day of the month `n` months from the current one (0 = this month).
- `fixed`: the ISO date in `date`.
- `none`: no date. Use it for a deadline when the brief sets none, and when the brief's rule cannot be expressed with the kinds above.
- `adjust`: applied last. `next_business_day` or `previous_business_day` moves a non-business day; `none` leaves it.

A deadline or offer date the DSL cannot express: use kind `none` and write the rule in `unmodeled_rules`. Code then checks nothing about it and the phone agent carries the rule.

## promise

- `min_amount`: `floor` when an amount below the floor may not be recorded. `max_amount`: `full` when more than the past-due amount may not be recorded.
- `business_day_required`, `not_after_deadline`, `not_before_today`: date conditions for a recordable promise.
- `one_per_product`: true when a product can have only one promise per call.
- `max_tool_attempts`: how many times `create_promise` may be called for ONE product in one call, the first try included. "If it fails, try once more" is 2.
- `wire_format`: date format of the promise tool (`YYYY-MM-DD` unless the API says otherwise).
- `success_sentence`: a sentence the brief requires after a recorded promise, verbatim, or `""`.
- `confirmation`: `batch` when several products are read back together and confirmed once, else `per_product`.

## callback

`used`: false when the brief has no callback. `allowed_days`: DateExprs for the days a callback may be on. `earliest_time` / `latest_time`: `HH:MM`, both included, `""` if unlimited. `after_now`: the callback must be later than the current time.

## transfer

`queues`: every queue the brief uses, with the closing pair that follows a transfer to it. `notes_on_transfer`: what happens to notes once a call is transferred. `forbidden`: the brief says no notes at all on a transferred call (the note tool is refused after a transfer, and notes are held back until the end of the call so that none exists when a transfer comes). `not_required`: notes may exist, but the remaining products need none after a transfer. `required`: a transfer changes nothing about notes. Use `not_required` when the brief has no notes or says nothing about it.

## notes

`used`: true only when the brief in force uses the note tool. `per_product_without_promise`: true when every product that ends without a promise needs a note. `reasons`: allowed reasons (tool API values). `timing`: `end_of_call` unless the brief requires a note the moment something happens.

## closing

- `table`: every outcome and reason pair the brief allows. `reason: ""` for an outcome without a reason.
- `precedence`: ordered rules for outcomes that the state of the call decides, first match wins. Only predicates that follow from tool results are available:
  - `transfer_done`: a transfer was registered. With `reason_source: transfer_queue` the reason comes from `transfer.queues`.
  - `promise_recorded`: at least one promise was recorded.
  - `callback_scheduled`: a callback was booked.
  - `identity_failed`: the wrong-answer limit was reached.
  - `promise_failed`: no promise was recorded and recording failed the allowed number of times for a product.
  - `all_products_noted_with`: every product has a note with reason `value`.
  - `any_note`: at least one note exists. With `reason_source: first_note_not` the reason is the note reason of the first product, in account order, whose note is not `value`.
- Order the list the way the brief resolves conflicts (for example a transfer after a recorded promise). Outcomes that depend on what was said (wrong number, declined, paid, dispute on a single product without notes) have no predicate: leave them out, the agent chooses them from the table.

## tools

For each of the seven tools:

- `roles`: which request field plays which role. Roles: every tool `customer`; `verify_identity`: `field`, `answer`; `create_promise`: `product`, `amount`, `date`; `add_note`: `product`, `reason`, `text`; `schedule_callback`: `date`, `time`; `transfer_to_agent`: `queue`; `close_call`: `outcome`, `reason`.
- `response`: which response field plays which role. `verify_identity`: `verified` (this answer was right), `all_verified` (the whole check is passed), `wrong_left` (wrong answers remaining), `hint`. `get_account`: `products` (the list), and inside one product `id`, `type`, `due` (past-due amount), `late` (days late).
- `success`: the response field and value that mean the action was carried out (`{"field": "ok", "equals": "true"}` for a boolean flag).
- `extra_fields`: request fields with a fixed value the brief prescribes. Leave other fields alone; the phone agent fills them from their API description.

## rules, unmodeled_rules, open_questions

See the skill. `overrides` is `""` when the rule replaces nothing.
