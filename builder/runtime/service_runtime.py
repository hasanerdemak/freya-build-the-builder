"""Hook code for the tools of a service-line agent. Embedded as text into every tool hook of the workflow.

It is identical for every case: which tools exist, where their request fields go and what they need
arrives as data in the config JSON.
"""


def _sv_main(_hook, _tool, _cfg_json, _vars, _config, _result):
    import json
    import re
    import datetime

    cfg = json.loads(_cfg_json)
    V = _vars if isinstance(_vars, dict) else dict()
    tool = (cfg.get('tools') or dict()).get(_tool) or dict()
    REFUSED = 'The call was NOT carried out. '

    def load(name):
        try:
            got = json.loads(V.get(name) or '[]')
            return got if isinstance(got, list) else []
        except Exception:
            return []

    def plain(value):
        return re.sub(r'[^0-9a-z]', '', str(value).lower())

    def shared_tail(a, b):
        a, b, n = plain(a), plain(b), 0
        while n < len(a) and n < len(b) and a[-1 - n] == b[-1 - n]:
            n += 1
        return n

    def typed(value, kind):
        if isinstance(value, str):
            value = value.strip()
        try:
            if kind == 'integer':
                return int(float(str(value).replace(',', '.')))
            if kind == 'number':
                return float(str(value).replace(',', '.'))
            if kind == 'boolean':
                return str(value).strip().lower() in ('true', 'yes', '1')
        except Exception:
            return value
        return value if kind != 'string' else str(value)

    def put(target, path, value):
        parts = [p for p in str(path).split('.') if p]
        node = target
        for p in parts[:-1]:
            if not isinstance(node.get(p), dict):
                node[p] = dict()
            node = node[p]
        node[parts[-1]] = value

    def lookup(root, path):
        # A dotted path into a service answer. A path that does not start at the root is also tried one level down.
        def walk(node, parts):
            for p in parts:
                if isinstance(node, dict) and p in node:
                    node = node[p]
                elif isinstance(node, list) and p.isdigit() and int(p) < len(node):
                    node = node[int(p)]
                else:
                    return None
            return node
        parts = [p for p in str(path).split('.') if p]
        found = walk(root, parts)
        if found is None and isinstance(root, dict):
            for child in root.values():
                if isinstance(child, dict):
                    found = walk(child, parts)
                    if found is not None:
                        break
        return found

    def holds(condition, root):
        # A small condition language over the fields of an answer: comparisons, missing(), present(), count(), and, or, not.
        tokens = re.findall(r'''\s*(>=|<=|==|!=|>|<|\(|\)|"[^"]*"|'[^']*'|-?\d+(?:\.\d+)?|[A-Za-z_][A-Za-z0-9_.]*)''', str(condition))
        pos = [0]

        def peek():
            return tokens[pos[0]] if pos[0] < len(tokens) else None

        def take():
            pos[0] += 1
            return tokens[pos[0] - 1]

        def operand():
            tok = take()
            if tok is None:
                return None
            if tok[0] in ('"', "'"):
                return tok[1:-1]
            if re.fullmatch(r'-?\d+(?:\.\d+)?', tok):
                return float(tok)
            if tok in ('true', 'false'):
                return tok == 'true'
            if tok == 'count' and peek() == '(':
                take()
                got = lookup(root, take())
                take()
                return float(len(got)) if isinstance(got, (list, dict, str)) else 0.0
            return lookup(root, tok)

        def compare(a, op, b):
            if a is None or b is None:
                return op == '!=' and (a is None) != (b is None)
            if isinstance(a, bool) or isinstance(b, bool):
                a, b = str(a).lower(), str(b).lower()
            elif isinstance(a, (int, float)) or isinstance(b, (int, float)):
                try:
                    a, b = float(a), float(b)
                except Exception:
                    a, b = str(a), str(b)
            if op == '==':
                return a == b
            if op == '!=':
                return a != b
            try:
                return (a > b) if op == '>' else (a >= b) if op == '>=' else (a < b) if op == '<' else (a <= b)
            except Exception:
                return False

        def atom():
            tok = peek()
            if tok == '(':
                take()
                value = either()
                take()
                return value
            if tok in ('missing', 'present') and pos[0] + 1 < len(tokens) and tokens[pos[0] + 1] == '(':
                take()
                take()
                got = lookup(root, take())
                take()
                empty = got is None or got == '' or got == [] or got == dict()
                return empty if tok == 'missing' else not empty
            left = operand()
            if peek() in ('>', '>=', '<', '<=', '==', '!='):
                op = take()
                return compare(left, op, operand())
            return bool(left)

        def negated():
            if peek() == 'not':
                take()
                return not negated()
            return atom()

        def both():
            value = negated()
            while peek() == 'and':
                take()
                right = negated()
                value = value and right
            return value

        def either():
            value = both()
            while peek() == 'or':
                take()
                right = both()
                value = value or right
            return value

        return bool(either())

    def flat(node, prefix, out):
        if isinstance(node, dict):
            for k, v in node.items():
                flat(v, (prefix + '.' if prefix else '') + str(k), out)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                flat(v, (prefix + '.' if prefix else '') + str(i), out)
        elif node is not None and node != '':
            out.append(prefix + '=' + str(node))
        return out

    NOT_CALLED = '(not called yet)'
    for name in cfg.get('tools') or dict():
        if not V.get('sv_mem_' + name):
            V['sv_mem_' + name] = NOT_CALLED

    if _hook == 'setup':
        return

    if _hook == 'pre':
        body = _config.get('body') if isinstance(_config, dict) else None
        body = body if isinstance(body, dict) else dict()
        out, key_parts = dict(), []
        for f in tool.get('fields') or []:
            arg, source = f.get('arg'), f.get('source')
            value = body.get(arg)
            if isinstance(value, str):
                value = value.strip()
            if isinstance(value, str) and value.startswith('{') and value.endswith('}'):
                value = ''                                   # a placeholder nobody filled
            if source == 'call_variable':
                held = V.get(f.get('value'))
                value = held if held not in (None, '') else value
            elif source == 'fixed':
                value = f.get('value') if value in (None, '') else value
            elif source == 'earlier_result':
                seen = load('sv_seen_' + str(f.get('from_tool')) + '_' + str(f.get('from_field')))
                if not seen:
                    raise Exception(REFUSED + 'The value for ' + str(arg) + ' must come from a result of ' + str(f.get('from_tool'))
                                    + ' in this call. CALL ' + str(f.get('from_tool')) + ' first.')
                if value in (None, '') and len(seen) == 1:
                    value = seen[0]
                if value not in seen:
                    # The agent model often rewrites an id it was given (masked digits, a guessed prefix). The value can
                    # only be one the service returned, so it is matched: same characters, else the one returned value
                    # that shares the longest ending with it, else the only value there is.
                    hits = [s for s in seen if plain(s) == plain(value)]
                    if not hits and value not in (None, ''):
                        ranked = sorted([(shared_tail(s, value), i) for i, s in enumerate(seen)], reverse=True)
                        if ranked[0][0] >= 3 and (len(ranked) == 1 or ranked[0][0] > ranked[1][0]):
                            hits = [seen[ranked[0][1]]]
                    if not hits and len(seen) == 1:
                        hits = [seen[0]]
                    if len(hits) != 1:
                        raise Exception(REFUSED + str(arg) + ' must be exactly one of the values that ' + str(f.get('from_tool'))
                                        + ' returned: ' + ', '.join(str(s) for s in seen[:12])
                                        + '. If it is not clear which one the customer means, ask the customer. Then call again with that exact value.')
                    value = hits[0]
            if value in (None, ''):
                if f.get('required'):
                    raise Exception(REFUSED + 'The value for ' + str(arg) + ' is missing. Get it as your instructions say and call again.')
                continue
            value = typed(value, f.get('type') or 'string')
            allowed = [str(a) for a in f.get('allowed') or []]
            if allowed and str(value) not in allowed:
                raise Exception(REFUSED + str(arg) + ' must be one of: ' + ', '.join(allowed) + '.')
            put(out, f.get('path') or arg, value)
            if source not in ('call_variable', 'fixed'):
                key_parts.append(str(arg) + '=' + str(value))
        for need in tool.get('only_after') or []:
            if not V.get('sv_ok_' + str(need)):
                raise Exception(REFUSED + 'CALL ' + str(need) + ' first, tell the customer what it returned, and only then call this tool.')
        key = '|'.join(key_parts)
        if tool.get('changes_something') and load('sv_done_' + _tool).count(key) >= max(1, int(tool.get('times_allowed') or 1)):
            raise Exception(REFUSED + 'This was already done successfully in this call with the same values. Do not do it again. '
                            'Tell the customer it is done and go on.')
        if tool.get('needs_customer_yes') and str(body.get('customer_said_yes') or '').strip().lower() != 'yes':
            raise Exception(REFUSED + 'The customer has not clearly said yes to exactly this action. Tell the customer what will be done, '
                            'with the details, ask for a clear yes or no, and call again only after a clear yes.')
        V['sv_pre_' + _tool] = key
        _config['body'] = out
        return

    if _hook == 'post':
        ok = isinstance(_result, dict) and str(_result.get('status') or 'success').lower() not in ('error', 'failed', 'failure')
        if isinstance(_result, dict):
            _result.pop('header', None)          # transport headers are long and say nothing the agent needs
        if not ok:
            return
        V['sv_ok_' + _tool] = '1'
        data = _result.get('data')
        for name in (cfg.get('collect') or dict()).get(_tool) or []:
            found = load('sv_seen_' + _tool + '_' + name)

            def walk(node):
                if isinstance(node, dict):
                    for k, v in node.items():
                        if k == name and isinstance(v, (str, int, float)) and not isinstance(v, bool) and str(v) != '':
                            if v not in found:
                                found.append(v)
                        else:
                            walk(v)
                elif isinstance(node, list):
                    for item in node:
                        walk(item)

            walk(data)
            V['sv_seen_' + _tool + '_' + name] = json.dumps(found[:60])
        if tool.get('changes_something'):
            done = load('sv_done_' + _tool)
            done.append(str(V.get('sv_pre_' + _tool) or ''))
            V['sv_done_' + _tool] = json.dumps(done[-30:])
        notes = []
        try:
            today = datetime.date.fromisoformat(str(V.get(cfg.get('now_variable') or 'now') or '')[:10])
        except Exception:
            today = None

        def dates(node, label):
            if isinstance(node, dict):
                for k, v in node.items():
                    dates(v, k)
            elif isinstance(node, list):
                for item in node:
                    dates(item, label)
            elif isinstance(node, str) and today is not None and len(notes) < 24:
                m = re.fullmatch(r'\s*(\d{2})\.(\d{2})\.(\d{4})\s*', node)
                iso = re.fullmatch(r'\s*(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?', node)
                try:
                    day = datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else (
                        datetime.date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))) if iso else None)
                except Exception:
                    day = None
                if day is not None:
                    gap = (day - today).days
                    when = 'the day of this call' if gap == 0 else (str(gap) + ' day(s) after the day of this call' if gap > 0
                                                                    else str(-gap) + ' day(s) before the day of this call, already past')
                    line = str(label) + ' ' + node.strip() + ' is ' + day.strftime('%A') + ', ' + when
                    if line not in notes:
                        notes.append(line)

        dates(data, '')
        digits = []

        def spell(node, label):
            if isinstance(node, dict):
                for k, v in node.items():
                    spell(v, k)
            elif isinstance(node, list):
                for item in node:
                    spell(item, label)
            elif isinstance(node, str) and 2 <= len(node) <= 8 and node.isdigit() and len(digits) < 16:
                line = str(label) + ' ' + node + ' digit by digit: ' + ' '.join(node)
                if line not in digits:
                    digits.append(line)

        spell(data, '')
        facts = []
        for rule in tool.get('result_facts') or []:
            try:
                if holds(rule.get('when'), data) and rule.get('say') and rule.get('say') not in facts:
                    facts.append(rule.get('say'))
            except Exception:
                pass
        waiting = []
        for other, spec in (cfg.get('tools') or dict()).items():
            needs = spec.get('only_after') or []
            if spec.get('changes_something') and _tool in needs and all(V.get('sv_ok_' + str(n)) for n in needs) and not load('sv_done_' + other):
                waiting.append(other)
        if isinstance(data, dict):
            if facts:
                data['facts'] = facts
            if waiting:
                data['not_done_yet'] = ('Not called in this call so far: ' + ', '.join(waiting) + '. Its action has NOT happened. It happens only when you CALL that tool, '
                                        'after the customer has clearly said yes. Never say it is done before that tool result says so.')
            if digits:
                data['digit_notes'] = digits
            if notes:
                data['date_notes'] = ['The day of this call is ' + today.strftime('%d.%m.%Y') + ', ' + today.strftime('%A')] + notes
            if tool.get('after_success'):
                data['what_to_do_next'] = tool.get('after_success')
            # Measured on the platform: in a later turn the agent no longer has an earlier tool result (it made up ids and
            # addresses it had been given). The prompt therefore shows what each tool returned, from these variables.
            # One variable per tool: answers of tool calls sent in one message are handled side by side.
            try:
                shown = dict((k, v) for k, v in data.items() if k not in ('what_to_do_next', 'not_done_yet'))
                entry = '; '.join(flat(shown, '', []))[:3500]
                asked = str(V.get('sv_pre_' + _tool) or '')
                entry = ('[asked with ' + asked + '] ' if asked else '') + entry
                kept = [p for p in str(V.get('sv_mem_' + _tool) or '').split(' ### ') if p and p != NOT_CALLED and p != entry]
                V['sv_mem_' + _tool] = ' ### '.join((kept + [entry])[-2:])
            except Exception:
                pass
        return
