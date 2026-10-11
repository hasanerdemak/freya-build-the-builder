"""The three model roles. Each role loads exactly one skill and receives the original inputs.

analyst   -> skills/collections-policy  -> Policy IR
developer -> skills/freya-workflow      -> phone-agent prompt text
verifier  -> skills/verify-and-repair   -> evidenced issues for a bounded repair
"""
import hashlib
import json
import pathlib

import apispec
import ir as irmod
import llm

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
ROLE_SKILL = {
    "analyst": ("collections-policy", ["references/ir-reference.md", "references/example.md"]),
    "developer": ("freya-workflow", ["references/skeleton.md"]),
    "verifier": ("verify-and-repair", ["references/checks.md"]),
    "designer": ("service-line", []),
    "reviewer": ("service-line", []),
}
IR_REFERENCE = SKILLS / "collections-policy" / "references" / "ir-reference.md"
SKELETON_REFERENCE = SKILLS / "freya-workflow" / "references" / "skeleton.md"


def load_skill(role):
    """Resolve and read the skill of a role. Returns name, resolved paths, a hash over all its files, and the text."""
    name, refs = ROLE_SKILL[role]
    base = SKILLS / name
    files = [base / "SKILL.md"] + [base / r for r in refs]
    digest, parts = hashlib.sha256(), []
    for f in files:
        data = f.read_bytes().replace(b"\r\n", b"\n")
        digest.update(f.relative_to(SKILLS).as_posix().encode() + b"\0" + data + b"\0")
        parts.append(f'<file path="{f.relative_to(base).as_posix()}">\n{data.decode("utf-8")}\n</file>')
    return {"role": role, "name": name, "path": str(base), "files": [str(f.relative_to(ROOT).as_posix()) for f in files],
            "sha256": digest.hexdigest(), "text": f'<skill name="{name}">\n' + "\n".join(parts) + "\n</skill>"}


def skill_manifest():
    return [{k: v for k, v in load_skill(r).items() if k != "text"} for r in ROLE_SKILL]


def _briefs_block(briefs):
    out = []
    for i, b in enumerate(briefs):
        kind = "base brief, extended by the next one" if i < len(briefs) - 1 else "the brief to build for"
        out.append(f'<brief index="{i + 1}" file="{b["name"]}" role="{kind}">\n{b["text"]}\n</brief>')
    return "\n".join(out)


def _api_block(spec, raw_api=None):
    text = "<tool_api_digest>\n" + apispec.digest(spec) + "\n</tool_api_digest>"
    if raw_api:
        text += "\n<tool_api_openapi>\n" + raw_api + "\n</tool_api_openapi>"
    return text


def _ref(path):
    return f'<reference path="{path.relative_to(SKILLS).as_posix()}">\n{path.read_text(encoding="utf-8")}\n</reference>'


def _issues_block(issues):
    return "<issues_to_fix>\n" + json.dumps(issues, indent=1) + "\n</issues_to_fix>"


def run_analyst(briefs, spec, raw_api, timeout_s, previous=None, issues=None):
    skill = load_skill("analyst")
    task = ("Produce the Policy IR for the brief to build for, bound to this tool API. Follow the skill and the IR reference exactly."
            if not previous else
            "Your earlier Policy IR is below with issues found by the verifier and by the deterministic checks. Return the complete corrected IR. "
            "Change only what the issues require; keep everything else exactly as it is.")
    parts = [skill["text"], _api_block(spec, raw_api), _briefs_block(briefs)]
    if previous:
        parts += ["<previous_ir>\n" + json.dumps(previous, indent=1) + "\n</previous_ir>", _issues_block(issues)]
    parts.append("<task>\n" + task + "\n</task>")
    data, info = llm.call("\n\n".join(parts), irmod.IR_SCHEMA, timeout_s, "analyst: " + ("repair the Policy IR" if previous else "extract the Policy IR from the brief and tool API"))
    info.update(role="analyst", skill=skill["name"], skill_sha256=skill["sha256"])
    return irmod.normalize_ir(data), info


def run_developer(briefs, spec, ir, timeout_s, previous=None, issues=None):
    skill = load_skill("developer")
    task = ("Write the prompt text for this bank's phone agent. Follow the skill and the skeleton reference exactly."
            if not previous else
            "Your earlier prompt text is below with issues found by the verifier and by the deterministic checks. Return the complete corrected prompt text. "
            "Change only what the issues require.")
    parts = [skill["text"], _api_block(spec), _briefs_block(briefs), "<policy_ir>\n" + json.dumps(ir, indent=1) + "\n</policy_ir>"]
    if previous:
        parts += ["<previous_prompts>\n" + json.dumps(previous, indent=1) + "\n</previous_prompts>", _issues_block(issues)]
    parts.append("<task>\n" + task + "\n</task>")
    data, info = llm.call("\n\n".join(parts), irmod.PROMPTS_SCHEMA, timeout_s, "developer: " + ("repair the phone-agent prompt text" if previous else "write the phone-agent prompt text from the IR and brief"))
    info.update(role="developer", skill=skill["name"], skill_sha256=skill["sha256"])
    return data, info


def run_verifier(briefs, spec, raw_api, ir, timeout_s, prompts=None, round_no=1, earlier=None):
    skill = load_skill("verifier")
    parts = [skill["text"], _ref(IR_REFERENCE)]
    if prompts is not None:
        parts.append(_ref(SKELETON_REFERENCE))
    parts += [_api_block(spec, raw_api if prompts is None else None), _briefs_block(briefs)]
    if prompts is None:
        parts.append('<candidate type="policy_ir">\n' + json.dumps(ir, indent=1) + "\n</candidate>")
        task = "Verify the candidate Policy IR against the brief and the tool API with the check list for a Policy IR."
    else:
        parts.append("<policy_ir>\n" + json.dumps(ir, indent=1) + "\n</policy_ir>")
        parts.append('<candidate type="prompt_text">\n' + json.dumps(prompts, indent=1) + "\n</candidate>")
        task = "Verify the candidate prompt text against the brief and the IR with the check list for prompt text. Report only issues with target `prompts`."
    if earlier:
        parts.append("<issues_from_previous_round>\n" + json.dumps(earlier, indent=1) + "\n</issues_from_previous_round>")
        task += f" This is round {round_no}: confirm the earlier issues are fixed and report only critical or major issues that remain or were introduced."
    parts.append("<task>\n" + task + "\n</task>")
    what = "Policy IR" if prompts is None else "prompt text"
    data, info = llm.call("\n\n".join(parts), irmod.ISSUES_SCHEMA, timeout_s, f"verifier: check the {what} against the original brief and tool API (round {round_no})")
    info.update(role="verifier", skill=skill["name"], skill_sha256=skill["sha256"])
    return data, info
