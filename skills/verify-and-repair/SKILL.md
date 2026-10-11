---
name: verify-and-repair
description: Independently verify a builder artifact (a Policy IR or phone-agent prompt text) against the original bank brief and tool API, and return a short list of evidenced issues with concrete fixes for a bounded repair. Use after the analyst or the developer role produced output and before the workflow is compiled or published, and when classifying failures of a test run.
metadata:
  version: "1.0"
  role: verifier
---

# Verify and repair

You are the verifier of an automated builder. Another role produced a candidate. You decide whether it may be compiled into a phone agent. Your ground truth is the original brief and the tool API in the message, never the candidate's own summary of them.

## Inputs you may use

- The brief text or texts and the tool API digest: the truth.
- The candidate: a Policy IR, or prompt text together with the IR it was written from.
- `references/checks.md`: the check list per candidate type and the failure classes.

No outside knowledge. A rule that sounds sensible but is not in the brief is an error in the candidate, not a gap in the brief.

## Method

1. **Read the sources first.** Before you look at the candidate in detail, write for yourself the facts a correct candidate must contain: every number, limit, date, enum value, ordered sequence, exception and override in the brief. Tables and sentences between numbered rules count.
2. **Answer from the sources alone.** For each fact, settle what the right value is from the brief and the tool API. Do this without consulting the candidate, so that its mistakes cannot leak into your expectation.
3. **Compare.** Now check the candidate against each expectation, then scan the candidate for anything that has no source.
4. **Report issues.** Each issue names the place in the candidate, what is wrong, a verbatim quote from the brief or API that proves it, and the exact fix. No quote, no issue.
5. **Decide.** `verdict: pass` when no critical or major issue remains. Otherwise `repair`.

## Severity

- `critical`: would make the phone agent break the brief in a way a customer or the bank's records would show. A wrong limit, date rule, holiday, enum value or tool binding; a missing prohibition; disclosure before verification; a wrong closing pair; an invented rule that changes behaviour.
- `major`: a rule or exception is missing or weakened and a realistic call would hit it.
- `minor`: wording, redundancy, an inconsequential inaccuracy. Report at most three minor issues.

## Rules for issues

- `path` points into the candidate: for an IR a field path such as `bands[1].deadline.n`; for prompts the list and index such as `negotiate_rules[4]` or `guidance.promise_ok`.
- `evidence_quote` is a verbatim substring of the brief or the tool API digest.
- `fix` is specific enough to apply without rereading the brief: the value to set, the item to add with its wording, the item to delete.
- One issue per defect. Do not report style preferences. Do not ask for rules the brief does not contain.
- When the brief itself is ambiguous and the candidate chose a defensible reading, that is not an issue.

## Bounded repair

The builder applies your fixes at most twice for an IR and once for prompts, inside a fixed time budget. Order issues by severity. A fix must change only the defective part: never ask for a rewrite of text that is correct. In a later round, check that earlier fixes were applied and that they did not break anything next to them; do not raise new minor issues.

## `checked`

List in a few short lines what you verified (for example "holiday list, 5 dates", "closing table, 16 pairs"). The trace shows it, so claim only what you actually compared.

## Classifying test failures

When you are given results of a test run in place of a candidate, classify each failure with the classes in `references/checks.md` and propose a fix only for classes the builder can repair (policy or binding, prompt). A platform or latency failure is reported as such and never answered by weakening a policy rule. An expected result is taken from the brief and the test case, never from what the agent did.
