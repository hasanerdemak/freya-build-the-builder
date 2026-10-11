# Skill research

Source-to-design mapping for the three builder skills. Researched on 2026-10-11 between 08:00 and 09:00 UTC. Every source below was opened in this session unless marked otherwise. No text was copied from any source; ideas were adapted and are attributed here. No repository script was executed and no plugin was installed.

A paper or a method is not a ready skill. No open-source skill for collections policy extraction or for Freya workflows was found, and none was assumed to exist; all three `SKILL.md` files are written for this builder.

## Shared: skill format

| Source | Reviewed | Idea taken | Adapted | Rejected |
|---|---|---|---|---|
| Agent Skills specification, https://agentskills.io/specification (agentskills/agentskills) | Whole specification page | `SKILL.md` with `name` and `description` frontmatter, `name` equal to the directory name, optional `metadata` map, `references/` one level deep, body under 500 lines, progressive disclosure | Frontmatter of all three skills follows it. Reference files hold the long material (`ir-reference.md`, `skeleton.md`, `checks.md`) | `scripts/` and `allowed-tools`: the roles run with no tools at all, so there is nothing to pre-approve |
| Anthropic skills repository, https://github.com/anthropics/skills, file `skills/skill-creator/SKILL.md` (main branch, fetched 2026-10-11; README states many skills are Apache 2.0, the skill's own frontmatter states no license) | Writing style, progressive disclosure, description and evaluation sections, read through a fetched summary | Imperative instructions, explain the reason instead of stacking capitalised MUSTs, explicit output template, a description that says what the skill does and when to use it | Each skill opens with the role and the consequence of its output, then method, invariants and failure behaviour | Its evaluation harness (`evals.json`, viewer scripts): out of scope for the time available. Skill behaviour is tested through builder runs |
| OpenAI skills catalogue, https://github.com/openai/skills (marked deprecated in favour of the plugins repository; per-skill `LICENSE.txt`) | README only | Confirms that the same open standard is used across vendors | Nothing beyond the format | Its curated skills: none concerns policy extraction, workflow compilation or verification |

## A. `skills/collections-policy`

| Source | Reviewed | Idea taken | Adapted | Rejected |
|---|---|---|---|---|
| Starter kit v3 briefs and tool API (`kit/briefs/brief-a.md`, `brief-b.md`, `kit/tools/openapi.json`), event material, no stated license | Complete files | The parts a collections brief has (who may be spoken to, identity check, stating the debt, promise limits, special situations, closing table) and how a second document overrides a first ("applies unless a rule below replaces it") | Used as a recognition list ("what to recognise"), never as default values. The structural example in the skill uses an invented bank with other fields and numbers | Any public amount, holiday, identity field or outcome name as a default |
| Kit runner and scorer (`kit/tools/run_cases.py:41-43`, `kit/tools/score.py:33`) | Persona builder and ledger scorer | The organisers' own code mentions other identity fields and other outcome names, which shows what must be parametric | Identity questions carry a format (`date`, `digits`, `year`, `text`) and normalisation steps; outcomes and reasons are data bound to the tool API | Hardcoding those names |
| Codex consultation 01 (from memory, not re-verified by Codex): W3C PROV-O, https://www.w3.org/TR/prov-o/ | Not opened | Keep the source entity, the derived rule and the revision relation apart | `rules[]` holds id, section, verbatim quote, kind and `overrides`; the deterministic checker searches each quote in the brief | The ontology itself; a flat rule list is enough here |

Design consequences: rule inventory first, then overrides, then binding to the API, then typed fields; `enforced_by` and `stage` per rule; `unmodeled_rules` and `open_questions` for gaps; no rule without a quote.

## B. `skills/freya-workflow`

| Source | Reviewed | Idea taken | Adapted | Rejected |
|---|---|---|---|---|
| Starter kit skill `kit/.claude/skills/build-phone-agent/SKILL.md` | Complete | "Move to the next step on a tool result, not on what the customer says", "prefer rules in routes and code over rules in prompts", "take every tool field and allowed value from the tool API" | The skeleton routes only on hook-written variables; the skill tells the developer role what code already enforces and what only text can carry | Its build loop (write the JSON freely, test on practice customers, fix up to three times): hidden briefs have no practice customers |
| Kit `docs/workflow-format.md` | Complete | Three model calls per turn; routing never sees the dialogue prompt; do not name nodes or routing in `task_message`; refer to tools by name and say when to call | Rules for writing `contact_rules` and `negotiate_rules`; the ban on mentioning steps, nodes or code | `extraction_schema`, `matches_intent`, global nodes, `context_variables` (reported as silently dropped on save) |
| Kit `docs/tools-and-code.md` | Complete | `default` bakes and `const` does not; mapping paths start at `data.`; tool descriptions as triggers; the `what_to_do_next` post-hook that fixes silence and ignored negative results; no double curly braces in code | Guidance keys: the runtime writes the mechanical half of `what_to_do_next` and the developer role writes the brief-specific half. The compiler and validator enforce the wiring rules | Lifecycle `close_call`, background tools, `code_exec` as a model-called tool (unverified) |

Design consequences: the developer role writes prose only; a fixed list of guidance keys with placeholders; forbidden words may not appear in pre-verification text even inside a prohibition; items are short and say counts and closing pairs literally.

## C. `skills/verify-and-repair`

| Source | Reviewed | Idea taken | Adapted | Rejected |
|---|---|---|---|---|
| Dhuliawala et al., "Chain-of-Verification Reduces Hallucination in Large Language Models", https://arxiv.org/abs/2309.11495 (2023) | Abstract | Plan verification questions, answer them independently of the draft so the draft cannot bias the answers, then compare | Method steps 1 to 3 of the skill: establish the expected facts from the brief and API alone, only then compare with the candidate. The verifier is a separate call that receives the original inputs | Running it inside one model call that also wrote the draft |
| Madaan et al., "Self-Refine: Iterative Refinement with Self-Feedback", https://arxiv.org/abs/2303.17651 (2023) | Abstract | Structured feedback followed by a targeted revision | Issues carry path, evidence quote and concrete fix; repair changes only what the issues name | Open-ended iteration: the builder caps repairs at two for the IR and one for prompts inside the run budget, and self-feedback alone is not treated as evidence |
| Kit README, "How you are scored" and "When something does not work" | Complete | Ledger, policy and honesty are separate checks; slow model turns are an infrastructure cause | Failure classes in `references/checks.md` separate policy, conversation, routing, tool and platform failures | Using a judge verdict or a transcript claim as a substitute for the ledger |

Design consequences: no issue without a verbatim quote; severity defined by what a customer or the ledger would show; expected values never derived from the candidate or from agent behaviour; platform failures are reported and never answered by weakening policy.

## How the skills are used

`builder/roles.py` maps one role to one skill and loads the skill file and its references into that role's model call: analyst to `collections-policy`, developer to `freya-workflow`, verifier to `verify-and-repair`. Every trace lists the resolved files and a SHA-256 per skill (`SKILL` lines), and each `MODEL` line names the skill and hash used. The phone agent never receives a skill; it receives only the text and data derived from its own brief.
