# Check lists

## Candidate: Policy IR

Ground truth is the brief in force (after overrides) and the tool API.

1. Call variables: names exactly as the brief lists them.
2. Opening line: contains what the brief requires in the first sentence and nothing the brief forbids before verification; variables exist.
3. Identity: the questions, their order, the `field` values (must be allowed values of the tool API), answer formats, the wrong-answer limit, hints and whether they count.
4. Calendar: weekend days, every holiday date, the meaning of "this" and "next" weekday.
5. Products: every product type of the tool API has a label and the floor the brief gives for it; field names exist in the API.
6. Bands: boundaries (both ends included), the deadline rule of each band (count business days versus calendar days carefully, "after today" versus "from today"), persuasion attempts.
7. Persuasion: amount and date of each attempt, which bands it applies to.
8. Promise: amount limits, date conditions, one per product, tool retry count (total tries per product), prescribed sentence verbatim, confirmation scope.
9. Callback: allowed days, time window, later-than-now.
10. Transfer: every queue with its closing pair; the note prohibition.
11. Notes: used or not in the brief in force, reasons, when they are required.
12. Closing: every pair of the brief's table and no other; precedence order matches how the brief resolves conflicts; pairs are allowed by the tool API.
13. Tool bindings: every role points at a real request or response field; the success field.
14. Rule inventory: every numbered rule and every table of the brief is present; quotes are verbatim; overrides point at the right rule; nothing is listed that the brief does not say.
15. `enforced_by` and `stage`: a rule that only speech can satisfy is not marked `code`; a rule that applies before verification is not staged `negotiate`.

For a brief made of raw material: check each override separately. A memo that changes one value leaves the rest of the changed rule in force. A code list defines meanings; it does not create rules.

## Candidate: prompt text

Ground truth is the brief and the IR.

1. Coverage: every IR rule with `enforced_by` of `prompt` or `both` is carried by an item in the right step.
2. Fidelity: no item changes a count, an order, a condition or a prescribed sentence of the brief.
3. Invention: no item, guidance or description adds a rule, an offer, a courtesy or an explanation the brief does not contain.
4. Disclosure: `system_rules`, `contact_rules` and the guidance used before verification contain none of the forbidden words, and nothing that tells a third party why the bank calls.
5. Closing: every closing pair named in the text is in the IR table and fits the situation it is attached to.
6. Tools: only the seven tool names; each is said to be called in the step that has it (contact: verify_identity, schedule_callback, transfer_to_agent, close_call; negotiate: create_promise, add_note, transfer_to_agent, close_call).
7. Facts: no figure, date or holiday is restated in the text when code supplies it; the text points to the fact blocks.
8. Form: no double curly braces; guidance placeholders are from the allowed list; nothing mentions nodes, variables, hooks or code.

## Failure classes for test runs

| Class | Signs | Repairable by the builder |
|---|---|---|
| policy or binding | the ledger shows a wrong value that follows from the IR (date, amount, enum, field) | yes, IR |
| conversation | the agent said or asked something the brief forbids, or skipped a required sentence | yes, prompt text |
| routing or state | the right tool call was never possible or the call did not move on | no, builder source |
| tool or ledger | a tool was not called, called twice, or its failure was reported as success | prompt text, or builder source when a guard is missing |
| platform | no call started, timeouts, slow turns, customer hung up after silence | no; report and rerun |
