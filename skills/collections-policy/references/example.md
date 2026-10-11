# Structural example (made-up bank, shape only)

Every value below is invented for a bank that does not exist. Do not reuse any of it.

Brief fragment:

> **Mail, 3 March.** Callers must give their postcode and then their year of birth. Two wrong answers end the check.
> **Policy extract 4.2.** A promise may be dated up to 5 calendar days from today. If that day is closed, use the working day before it. Closed days: Sundays and 2031-05-01.
> **Memo M-7 (replaces extract 4.2, first sentence).** The limit is 3 calendar days for accounts over 60 days late.
> **Code list.** `settled` = customer already paid. `ptp` = promise taken.

Resulting IR fragments:

```json
{
 "identity": {"ordered": true, "max_wrong_answers": 2,
  "questions": [
   {"field": "postcode", "ask": "What is the postcode of your registered address?", "format": "text", "length": 0, "normalize": [], "numeric_date_order": "", "wire_format": ""},
   {"field": "birth_year", "ask": "In which year were you born?", "format": "year", "length": 4, "normalize": [], "numeric_date_order": "", "wire_format": ""}],
  "hints": []},
 "calendar": {"weekend_days": ["sunday"], "holidays": ["2031-05-01"], "week_start": "monday", "this_weekday": "unspecified", "next_weekday": "unspecified"},
 "bands": [
  {"name": "over_60", "field": "days_overdue", "min": 61, "max": 100000, "persuasion_attempts": 0,
   "deadline": {"kind": "calendar_days_after_today", "n": 3, "week_offset": 0, "roll_if_today": false, "adjust": "previous_business_day", "date": ""}},
  {"name": "standard", "field": "days_overdue", "min": 0, "max": 60, "persuasion_attempts": 0,
   "deadline": {"kind": "calendar_days_after_today", "n": 5, "week_offset": 0, "roll_if_today": false, "adjust": "previous_business_day", "date": ""}}],
 "rules": [
  {"id": "extract-4.2a", "section": "Policy extract 4.2", "quote": "A promise may be dated up to 5 calendar days from today.", "kind": "obligation", "summary": "Promise date limit of 5 calendar days", "overrides": "", "enforced_by": "code", "stage": "negotiate"},
  {"id": "memo-M7", "section": "Memo M-7", "quote": "The limit is 3 calendar days for accounts over 60 days late.", "kind": "override", "summary": "3 days when more than 60 days late", "overrides": "extract-4.2a", "enforced_by": "code", "stage": "negotiate"}]
}
```

What the example shows:

- identity fields and the limit come from the mail, the field names from the tool API;
- the memo replaces one sentence for one group only, so two bands exist and the general rule stays for the rest;
- the second sentence of the extract (use the working day before) is not replaced and applies to both bands through `adjust`;
- the code list tells which API value means what; it adds no rule by itself.
