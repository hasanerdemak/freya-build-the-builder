"""Policy runtime that runs INSIDE the Freya workflow (code nodes and tool hooks).

The compiler embeds this source verbatim, followed by one entry line, in every hook and code node.
It is identical for every brief: all bank policy arrives as data in the config JSON.

Constraints of the Freya sandbox this file respects:
- only json, re and datetime are imported;
- the text contains no double curly braces, because the platform templates them;
- everything lives inside one function, so the snippet does not depend on how the sandbox
  scopes top-level names.
"""


def _rt_main(_hook, _tool, _cfg_json, _vars, _config, _result):
    import json, re, datetime

    cfg = json.loads(_cfg_json)
    ir = cfg.get('ir') or dict()
    api = cfg.get('api') or dict()
    V = _vars if isinstance(_vars, dict) else dict()
    WD = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    MON = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
           'October', 'November', 'December']
    cal = ir.get('calendar') or dict()
    ident = ir.get('identity') or dict()
    prom = ir.get('promise') or dict()
    notes_cfg = ir.get('notes') or dict()
    closing = ir.get('closing') or dict()
    transfer_cfg = ir.get('transfer') or dict()
    callback_cfg = ir.get('callback') or dict()
    guidance = cfg.get('guidance') or dict()

    # ---------- small helpers ----------
    def low(x):
        return str(x if x is not None else '').strip().lower()

    def truthy(x):
        return low(x) in ('true', '1', 'yes', 'ok')

    def money(x):
        try:
            return '%.2f' % round(float(x) + 1e-9, 2)
        except Exception:
            return str(x)

    def G(key, default):
        t = guidance.get(key)
        return str(t).strip() if t else default

    def fill(text, values):
        out = str(text)
        for k in values:
            out = out.replace('{' + k + '}', str(values[k]))
        return out

    def join(parts):
        return ' '.join([str(p).strip() for p in parts if p and str(p).strip()])

    def num(x, default):
        try:
            return float(x)
        except Exception:
            return default

    # ---------- calendar ----------
    weekend = []
    for name in (cal.get('weekend_days') or ['saturday', 'sunday']):
        for i in range(7):
            if WD[i].lower() == low(name):
                weekend.append(i)
    holidays = set([str(h)[:10] for h in (cal.get('holidays') or [])])

    def is_bd(d):
        return d.weekday() not in weekend and d.isoformat() not in holidays

    def step(d, n):
        return d + datetime.timedelta(days=n)

    def next_bd(d):
        k = 0
        while not is_bd(d) and k < 60:
            d = step(d, 1)
            k += 1
        return d

    def prev_bd(d):
        k = 0
        while not is_bd(d) and k < 60:
            d = step(d, -1)
            k += 1
        return d

    def add_bd(d, n):
        k = 0
        while n > 0 and k < 400:
            d = step(d, 1)
            k += 1
            if is_bd(d):
                n -= 1
        return d

    def week_start_index():
        ws = low(cal.get('week_start') or 'monday')
        for i in range(7):
            if WD[i].lower() == ws:
                return i
        return 0

    def week_first_day(d, offset):
        back = (d.weekday() - week_start_index()) % 7
        return step(d, -back + 7 * offset)

    def last_bd_of_week(d, offset):
        first = week_first_day(d, offset)
        x = step(first, 6)
        k = 0
        while not is_bd(x) and k < 7:
            x = step(x, -1)
            k += 1
        return x

    def adjust(d, how):
        how = low(how)
        if how == 'next_business_day':
            return next_bd(d)
        if how == 'previous_business_day':
            return prev_bd(d)
        return d

    def eval_date(expr, today):
        if not isinstance(expr, dict):
            return None
        kind = low(expr.get('kind'))
        n = int(expr.get('n') or 0)
        if kind == 'today':
            d = today
        elif kind == 'business_days_after_today':
            d = add_bd(today, n)
        elif kind == 'calendar_days_after_today':
            d = step(today, n)
        elif kind == 'last_business_day_of_week':
            off = int(expr.get('week_offset') or 0)
            d = last_bd_of_week(today, off)
            if d < today or (d == today and expr.get('roll_if_today')):
                d = last_bd_of_week(today, off + 1)
        elif kind == 'end_of_month':
            y = today.year
            m = today.month + n
            while m > 12:
                m -= 12
                y += 1
            nm = datetime.date(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1)
            d = step(nm, -1)
        elif kind == 'fixed':
            d = datetime.date.fromisoformat(str(expr.get('date'))[:10])
        else:
            return None
        return adjust(d, expr.get('adjust'))

    def fmt_date(d):
        return WD[d.weekday()] + ' ' + str(d.day) + ' ' + MON[d.month - 1] + ' ' + str(d.year) + ' (' + d.isoformat() + ')'

    def now_parts():
        cv = ir.get('call_variables') or dict()
        raw = str(V.get(cv.get('now') or 'now') or '')
        m = re.match(r'^\s*(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{1,2}):(\d{2}))?', raw)
        try:
            if m:
                d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                t = ('%02d:%s' % (int(m.group(4)), m.group(5))) if m.group(4) else '00:00'
                return d, t
            st0 = load_state()
            if st0.get('today'):
                return datetime.date.fromisoformat(str(st0.get('today'))[:10]), st0.get('now_time') or '00:00'
        except Exception:
            pass
        return datetime.date.today(), '00:00'

    # <only:pre,post>
    def parse_date(text, order):
        s = str(text or '').strip()
        m = re.match(r'^(\d{4})[./\- ](\d{1,2})[./\- ](\d{1,2})', s)
        try:
            if m:
                return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            m = re.match(r'^(\d{1,2})[./\- ](\d{1,2})[./\- ](\d{4})$', s)
            if m:
                a = int(m.group(1))
                b = int(m.group(2))
                if low(order) == 'mdy':
                    return datetime.date(int(m.group(3)), a, b)
                return datetime.date(int(m.group(3)), b, a)
            words = re.sub(r'(st|nd|rd|th)\b', '', s.lower().replace(',', ' ')).split()
            day = None
            month = None
            year = None
            for w in words:
                if w.isdigit():
                    if len(w) == 4:
                        year = int(w)
                    elif day is None:
                        day = int(w)
                else:
                    for i in range(12):
                        if len(w) >= 3 and MON[i].lower().startswith(w[:3]) and MON[i].lower().startswith(w):
                            month = i + 1
            if day and month and year:
                return datetime.date(year, month, day)
        except Exception:
            return None
        return None

    def wire_date(d, fmt):
        f = str(fmt or 'YYYY-MM-DD')
        return f.replace('YYYY', '%04d' % d.year).replace('MM', '%02d' % d.month).replace('DD', '%02d' % d.day)

    def parse_time(text):
        s = low(text).replace('.', ':')
        m = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$', s)
        if not m:
            return None
        h = int(m.group(1))
        mi = int(m.group(2) or 0)
        if m.group(3) == 'pm' and h < 12:
            h += 12
        if m.group(3) == 'am' and h == 12:
            h = 0
        if h > 23 or mi > 59:
            return None
        return '%02d:%02d' % (h, mi)

    # </only>
    # ---------- state ----------
    def load_state():
        raw = V.get('rt_state')
        try:
            if isinstance(raw, dict):
                return raw
            if raw:
                got = json.loads(raw)
                if isinstance(got, dict):
                    return got
        except Exception:
            pass
        return dict()

    def save_state(st):
        V['rt_state'] = json.dumps(st)

    def init_state(st):
        st.setdefault('id', dict(passed=[], wrong=0, status='pending'))
        st.setdefault('products', [])
        st.setdefault('promises', dict())
        st.setdefault('fails', dict())
        st.setdefault('notes', dict())
        st.setdefault('transfer', '')
        st.setdefault('callback', '')
        st.setdefault('closed', '')
        st.setdefault('blocks', 0)
        # Measured: tool calls of one message run at the same time, and each post-hook saves the whole state, so
        # one can undo the other. A promise or note is therefore also kept in a variable of its own and merged here.
        try:
            for x in st.get('products') or []:
                xid = x.get('id')
                if xid and xid not in st['promises'] and V.get('rt_p_' + str(xid)):
                    st['promises'][xid] = json.loads(V.get('rt_p_' + str(xid)))
                if xid and xid not in st['notes'] and V.get('rt_n_' + str(xid)):
                    st['notes'][xid] = str(V.get('rt_n_' + str(xid)))
                # Measured: when notes go out in one message, every post-hook reads the record of the last one, so
                # only that product looks noted. The pre-hooks run one after another and are reliable: a note whose
                # request was sent, with answers back and no failure reported, is written at the bank.
                if xid and xid not in st['notes'] and V.get('rt_ns_' + str(xid)) and V.get('rt_np') == '1' and not V.get('rt_nf'):
                    st['notes'][xid] = str(V.get('rt_ns_' + str(xid)))
        except Exception:
            pass
        return st

    # ---------- api bindings ----------
    def tool_ir(tool):
        return (ir.get('tools') or dict()).get(tool) or dict()

    def role(tool, name):
        return (tool_ir(tool).get('roles') or dict()).get(name) or ''

    def resp_role(tool, name):
        return (tool_ir(tool).get('response') or dict()).get(name) or ''

    # <only:post>
    def data_of(result):
        if isinstance(result, dict):
            d = result.get('data')
            if isinstance(d, dict):
                return d
        return dict()

    def call_ok(tool, result):
        if not isinstance(result, dict):
            return False
        code = result.get('status_code')
        try:
            if code is not None and int(code) >= 400:
                return False
        except Exception:
            pass
        if low(result.get('status')) not in ('', 'success'):
            return False
        suc = tool_ir(tool).get('success') or dict()
        f = suc.get('field') or ''
        if not f:
            return True
        d = data_of(result)
        if f not in d:
            return False
        want = low(suc.get('equals') if suc.get('equals') not in (None, '') else 'true')
        return low(d.get(f)) == want

    # </only>
    # ---------- products ----------
    # <only:plan>
    def raw_products():
        acc = tool_ir('get_account').get('response') or dict()
        path = acc.get('products') or 'products'
        raw = V.get('rt_products_raw')
        if isinstance(raw, str) and raw.strip():
            try:
                raw = json.loads(raw)
            except Exception:
                raw = None
        if isinstance(raw, dict):
            raw = raw.get(path) or raw.get('items')
        if isinstance(raw, list) and raw:
            return [p for p in raw if isinstance(p, dict)]
        found = dict()
        for k in list(V.keys()):
            if not isinstance(k, str):
                continue
            m = re.search(r'\.data\.' + re.escape(path) + r'[.\[](\d+)\]?\.(.+)$', k)
            if m and 'account' in k:
                found.setdefault(int(m.group(1)), dict())[m.group(2)] = V.get(k)
        return [found[i] for i in sorted(found.keys())]

    def eval_amount(expr, raw, due):
        if not isinstance(expr, dict):
            return None
        kind = low(expr.get('kind'))
        if kind == 'full':
            return due
        if kind == 'fixed':
            return round(num(expr.get('amount'), 0.0), 2)
        if kind == 'field':
            v = raw.get(expr.get('field'))
            return None if v is None else round(num(v, 0.0), 2)
        if kind == 'percent_of_field':
            v = raw.get(expr.get('field'))
            return None if v is None else round(num(v, 0.0) * num(expr.get('percent'), 0.0) / 100.0 + 1e-9, 2)
        return None

    def build_products(st, today):
        acc = tool_ir('get_account').get('response') or dict()
        types = (ir.get('account') or dict()).get('product_types') or []
        out = []
        idx = 0
        for raw in raw_products():
            idx += 1
            pid = str(raw.get(acc.get('id') or 'product_id') or ('p' + str(idx)))
            ptype = str(raw.get(acc.get('type') or 'type') or '')
            due = round(num(raw.get(acc.get('due') or 'past_due'), 0.0), 2)
            late = raw.get(acc.get('late') or 'days_late')
            tdef = None
            for t in types:
                if low(t.get('type')) == low(ptype):
                    tdef = t
            if tdef is None:
                for t in types:
                    if low(t.get('type')) in ('*', 'default', 'any'):
                        tdef = t
            label = ptype.replace('_', ' ')
            floor = None
            floor_label = ''
            if tdef:
                label = str(tdef.get('label') or label)
                for k in raw:
                    label = label.replace('{' + str(k) + '}', str(raw.get(k)))
                floor = eval_amount(tdef.get('floor'), raw, due)
                floor_label = str(tdef.get('floor_label') or '')
            band = None
            for b in (ir.get('bands') or []):
                f = b.get('field') or ''
                if f:
                    v = num(raw.get(f), None)
                    if v is None:
                        continue
                    if b.get('min') is not None and v < num(b.get('min'), v):
                        continue
                    if b.get('max') is not None and v > num(b.get('max'), v):
                        continue
                band = b
                break
            deadline = eval_date(band.get('deadline'), today) if band else None
            offers = []
            if band:
                for a in (ir.get('persuasion') or []):
                    bs = [low(x) for x in (a.get('bands') or [])]
                    if bs and low(band.get('name')) not in bs:
                        continue
                    if int(a.get('attempt') or 0) > int(band.get('persuasion_attempts') or 0):
                        continue
                    d = eval_date(a.get('date'), today)
                    if low((a.get('date') or dict()).get('kind')) == 'deadline':
                        d = deadline
                    offers.append(dict(attempt=int(a.get('attempt') or 0), amount=low(a.get('amount')),
                                       date=d.isoformat() if d else ''))
            out.append(dict(id=pid, type=ptype, label=label, due=due, late=late, floor=floor,
                            floor_label=floor_label, band=(band.get('name') if band else ''),
                            attempts=(int(band.get('persuasion_attempts') or 0) if band else 0),
                            deadline=(deadline.isoformat() if deadline else ''), offers=offers))
        st['products'] = out
        return out

    # </only>
    # <only:pre,post>
    def find_product(st, value):
        ps = st.get('products') or []
        v = low(value)
        for p in ps:
            if low(p.get('id')) == v:
                return p
        if len(ps) == 1:
            return ps[0]
        hits = [p for p in ps if v and (v in low(p.get('label')) or v == low(p.get('type')))]
        if len(hits) == 1:
            return hits[0]
        return None

    def open_products(st):
        return [p for p in (st.get('products') or []) if p.get('id') not in st.get('promises') and p.get('id') not in st.get('notes')]

    # ---------- closing ----------
    def table_reasons(outcome):
        rs = []
        seen = False
        for row in (closing.get('table') or []):
            if low(row.get('outcome')) == low(outcome):
                seen = True
                rs.append(str(row.get('reason') or ''))
        return seen, rs

    def predicate(st, rule):
        w = low(rule.get('when'))
        arg = low(rule.get('value'))
        ns = st.get('notes') or dict()
        if w == 'transfer_done':
            return bool(st.get('transfer'))
        if w == 'promise_recorded':
            return len(st.get('promises') or dict()) + int(st.get('promises_unknown') or 0) > 0
        if w == 'callback_scheduled':
            return bool(st.get('callback'))
        if w == 'identity_failed':
            return (st.get('id') or dict()).get('status') == 'failed'
        if w == 'promise_failed':
            return len(st.get('promises') or dict()) + int(st.get('promises_unknown') or 0) == 0 and any(
                int(n) >= int(prom.get('max_tool_attempts') or 2) for n in (st.get('fails') or dict()).values())
        if w == 'all_products_noted_with':
            ps = st.get('products') or []
            return len(ps) > 0 and all(low(ns.get(p.get('id'))) == arg for p in ps)
        if w == 'any_note':
            return len(ns) > 0
        return False

    def forced_close(st):
        for rule in (closing.get('precedence') or []):
            if not predicate(st, rule):
                continue
            outcome = str(rule.get('outcome') or '')
            reason = str(rule.get('reason') or '')
            src = low(rule.get('reason_source'))
            if src == 'transfer_queue':
                for q in (transfer_cfg.get('queues') or []):
                    if low(q.get('queue')) == low(st.get('transfer')):
                        reason = str(q.get('close_reason') or '')
                        outcome = str(q.get('close_outcome') or outcome)
            elif src == 'first_note_not':
                skip = low(rule.get('value'))
                reason = ''
                for p in (st.get('products') or []):
                    r = (st.get('notes') or dict()).get(p.get('id'))
                    if r and low(r) != skip:
                        reason = str(r)
                        break
                if not reason:
                    continue
            return outcome, reason
        return None

    def close_words(pair):
        if not pair:
            return ''
        o_f = role('close_call', 'outcome') or 'outcome'
        r_f = role('close_call', 'reason') or 'reason'
        if pair[1]:
            return 'call close_call with ' + o_f + '="' + pair[0] + '" and ' + r_f + '="' + pair[1] + '"'
        return 'call close_call with ' + o_f + '="' + pair[0] + '" and no ' + r_f

    def notes_needed(st):
        if not notes_cfg.get('used') or not notes_cfg.get('per_product_without_promise'):
            return []
        if st.get('transfer') and low(transfer_cfg.get('notes_on_transfer')) != 'required':
            return []
        if (st.get('id') or dict()).get('status') != 'verified':
            return []
        return open_products(st)

    def wrap_up(st):
        need = notes_needed(st)
        if need:
            ids = ', '.join([p.get('id') + ' (' + p.get('label') + ')' for p in need])
            return join(['Products not settled yet: ' + ids + '.',
                         'If the customer confirmed a promise for one of them, record that promise first. For a product that ends without a promise:',
                         G('notes_pending', 'When nothing more can be agreed on them, call add_note once for each of them with its reason, then say goodbye and close the call.')])
        pair = forced_close(st)
        if pair:
            return join([G('farewell', 'Thank the customer and say goodbye,'), 'and in the same turn ' + close_words(pair) + '.'])
        return G('close_choose', 'When the conversation is finished, thank the customer, say goodbye and call close_call with the outcome and reason that the rules give for what happened.')

    # </only>
    # ---------- judging one tool call (pure) ----------
    # <only:pre,post>
    def verdict(ok, code, msg, fixed):
        return dict(ok=ok, code=code, msg=msg, fixed=fixed)

    def judge(tool, args, st, today, now_time):
        fixed = dict()
        ids = st.get('id') or dict()
        if st.get('closed'):
            return verdict(False, 'closed', 'The call is already closed. Do not call any tool and do not say anything else.', fixed)

        # <only:verify_identity>
        if tool == 'verify_identity':
            f_field = role(tool, 'field') or 'field'
            f_ans = role(tool, 'answer') or 'answer'
            qs = ident.get('questions') or []
            if ids.get('status') == 'failed':
                return verdict(False, 'id_over', join(['The identity check has failed and is over. Do not ask or check anything else.', G('id_failed', ''), wrap_up(st)]), fixed)
            if ids.get('status') == 'verified':
                return verdict(False, 'id_done', 'Identity is already verified. Do not call verify_identity again.', fixed)
            q = None
            for x in qs:
                if low(x.get('field')) == low(args.get(f_field)):
                    q = x
            pending = [x for x in qs if x.get('field') not in (ids.get('passed') or [])]
            if q is None or q.get('field') in (ids.get('passed') or []) or (ident.get('ordered', True) and pending and q is not pending[0]):
                if pending:
                    q = pending[0]
                    fixed[f_field] = q.get('field')
                else:
                    return verdict(False, 'id_done', 'All security questions are answered. Do not call verify_identity again.', fixed)
            ans = str(args.get(f_ans) if args.get(f_ans) is not None else '').strip()
            fmt = low(q.get('format'))
            ask = str(q.get('ask') or q.get('field'))
            if fmt == 'date':
                d = parse_date(ans, q.get('numeric_date_order'))
                # A caller may say a date in two parts (day and month, then the year). The first part is kept and joined.
                dpart = st.get('partial') if isinstance(st.get('partial'), dict) else dict()
                before = str(dpart.get(q.get('field')) or '')
                if d is None and before and ans:
                    d = parse_date(before + ' ' + ans, q.get('numeric_date_order')) or parse_date(ans + ' ' + before, q.get('numeric_date_order'))
                pieces = len(re.findall(r'[0-9]+', ans)) + (1 if re.search(r'[A-Za-z][A-Za-z][A-Za-z]', ans) else 0)
                if d is None and ans and len(ans) <= 24 and 1 <= pieces <= 2:
                    dpart[q.get('field')] = ans
                    st['partial'] = dpart
                    return verdict(False, 'id_incomplete', "Only part of the date was given ('" + ans + "'). It is kept. It was NOT checked and does NOT count as a wrong answer. Ask only for the part that is missing (a date needs day, month and year), then call verify_identity with what the customer says.", fixed)
                if d is not None:
                    dpart.pop(q.get('field'), None)
                if d is None:
                    return verdict(False, 'id_unreadable', join(['That answer could not be read as a complete date, so it was NOT checked and does NOT count as a wrong answer.', G('id_unreadable_date', 'Ask the customer to say the full date again with the month as a word.')]), fixed)
                fixed[f_ans] = wire_date(d, q.get('wire_format'))
            elif fmt in ('digits', 'year'):
                digits = re.sub(r'\D', '', ans)
                want = int(q.get('length') or (4 if fmt == 'year' else 0))
                # A caller may say a number in two parts. The first part is kept, and a second part that completes it is joined to it.
                part = st.get('partial') if isinstance(st.get('partial'), dict) else dict()
                earlier = str(part.get(q.get('field')) or '')
                if want and digits and len(digits) < want and earlier and len(earlier + digits) == want:
                    digits = earlier + digits
                if not digits or (want and len(digits) != want):
                    if want and digits and len(digits) < want:
                        part[q.get('field')] = digits
                        st['partial'] = part
                    return verdict(False, 'id_incomplete', join(['That answer is not complete (' + str(want if want else 'all') + ' digits are needed, ' + str(len(digits)) + ' were given), so it was NOT checked and does NOT count as a wrong answer.', G('id_incomplete', 'Ask the customer for the rest, then check the whole answer: the digits already given followed by the new ones.')]), fixed)
                part.pop(q.get('field'), None)
                fixed[f_ans] = digits
            else:
                v = ans
                ops = [low(o) for o in (q.get('normalize') or [])]
                if 'strip_spaces' in ops:
                    v = re.sub(r'\s+', '', v)
                if 'uppercase' in ops:
                    v = v.upper()
                if 'lowercase' in ops:
                    v = v.lower()
                if 'digits_only' in ops:
                    v = re.sub(r'\D', '', v)
                if not v:
                    return verdict(False, 'id_incomplete', 'No answer was given, so nothing was checked. Ask the question again: ' + ask, fixed)
                fixed[f_ans] = v
            return verdict(True, 'ok', '', fixed)
        # </only>

        # <only:create_promise>
        if tool == 'create_promise':
            f_p = role(tool, 'product') or 'product_id'
            f_a = role(tool, 'amount') or 'amount'
            f_d = role(tool, 'date') or 'date'
            if ids.get('status') != 'verified' or not st.get('products'):
                return verdict(False, 'not_ready', 'A promise cannot be recorded now. Follow the current step.', fixed)
            if st.get('transfer'):
                return verdict(False, 'after_transfer', join(['The call was handed over, so no promise is recorded now.', wrap_up(st)]), fixed)
            p = find_product(st, args.get(f_p))
            if p is None:
                return verdict(False, 'unknown_product', 'Unknown product. Use exactly one of these product ids: ' + ', '.join([x.get('id') + ' = ' + x.get('label') for x in st.get('products')]) + '.', fixed)
            pid = p.get('id')
            fixed[f_p] = pid
            if prom.get('one_per_product', True) and pid in (st.get('promises') or dict()):
                return verdict(False, 'duplicate', join(['A promise for ' + p.get('label') + ' is ALREADY recorded. Nothing new was recorded.', G('promise_duplicate', 'A recorded promise cannot be changed or recorded again in this call.'), wrap_up(st)]), fixed)
            if pid in (st.get('notes') or dict()):
                return verdict(False, 'promise_on_noted', join(['A note is already written for ' + p.get('label') + ', so no promise can be recorded for it in this call. Nothing was recorded.', wrap_up(st)]), fixed)
            if int((st.get('fails') or dict()).get(pid) or 0) >= int(prom.get('max_tool_attempts') or 2):
                return verdict(False, 'retries_used', join(['Recording for ' + p.get('label') + ' already failed the allowed number of times. Do not try again.', G('promise_fail_final', ''), wrap_up(st)]), fixed)
            amt = num(args.get(f_a), None)
            if amt is None or amt != amt or amt in (float('inf'), float('-inf')) or amt <= 0:
                return verdict(False, 'bad_amount', 'The amount is not a number. Nothing was recorded. Use the exact agreed amount.', fixed)
            amt = round(amt + 1e-9, 2)
            fixed[f_a] = amt
            d = parse_date(args.get(f_d), 'dmy')
            if d is None:
                return verdict(False, 'bad_date', 'The date is not an exact calendar date. Nothing was recorded. Use the calendar facts to find the exact date and send it as YYYY-MM-DD.', fixed)
            fixed[f_d] = wire_date(d, prom.get('wire_format'))
            vals = dict(product=p.get('label'), floor=money(p.get('floor')), full=money(p.get('due')), amount=money(amt))
            if low(prom.get('min_amount') or 'floor') == 'floor' and p.get('floor') is not None and amt < float(p.get('floor')) - 0.004:
                return verdict(False, 'below_floor', join([fill('Nothing was recorded: {amount} is below the lowest amount that can be recorded for {product}, which is {floor}.', vals), fill(G('amount_below_floor', 'Say it cannot be recorded and follow the rule for an amount that is too low.'), vals)]), fixed)
            if low(prom.get('max_amount') or 'full') == 'full' and amt > float(p.get('due')) + 0.004:
                return verdict(False, 'above_full', fill('Nothing was recorded: {amount} is more than the full past-due amount {full} of {product}. Read back the correct amount and get a new confirmation.', vals), fixed)
            dl = datetime.date.fromisoformat(p.get('deadline')) if p.get('deadline') else None
            vals['date'] = fmt_date(d)
            vals['deadline'] = fmt_date(dl) if dl else ''
            if prom.get('not_before_today', True) and d < today:
                return verdict(False, 'past_date', fill('Nothing was recorded: {date} is in the past. Today is ' + fmt_date(today) + '. Agree a valid date and read it back again.', vals), fixed)
            if prom.get('not_after_deadline', True) and dl and d > dl:
                return verdict(False, 'after_deadline', join([fill('Nothing was recorded: {date} is after the latest date that can be recorded for {product}, which is {deadline}.', vals), fill(G('date_after_deadline', 'Tell the customer the latest date you can record and offer it. Record only after a new clear yes.'), vals)]), fixed)
            if prom.get('business_day_required', True) and not is_bd(d):
                nb = next_bd(d)
                vals['next_business_day'] = fmt_date(nb)
                if dl and nb > dl:
                    return verdict(False, 'after_deadline', join([fill('Nothing was recorded: {date} is not a business day and the next business day is after the latest date {deadline} for {product}.', vals), fill(G('date_after_deadline', 'Tell the customer the latest date you can record and offer it. Record only after a new clear yes.'), vals)]), fixed)
                return verdict(False, 'not_business_day', join([fill('Nothing was recorded: {date} is not a business day. The next business day is {next_business_day}.', vals), fill(G('date_not_business_day', 'Offer the next business day and record only after a new clear yes.'), vals)]), fixed)
            return verdict(True, 'ok', '', fixed)
        # </only>

        # <only:add_note>
        if tool == 'add_note':
            f_p = role(tool, 'product') or 'product_id'
            f_r = role(tool, 'reason') or 'reason'
            if not notes_cfg.get('used'):
                return verdict(False, 'notes_unused', join(['Notes are not used in this call. Nothing was written.', wrap_up(st)]), fixed)
            if st.get('transfer') and low(transfer_cfg.get('notes_on_transfer')) == 'forbidden':
                return verdict(False, 'note_after_transfer', join(['No note may be written when the call is handed over. Nothing was written.', wrap_up(st)]), fixed)
            p = find_product(st, args.get(f_p))
            if p is None or (ids.get('status') != 'verified'):
                return verdict(False, 'unknown_product', 'Unknown product for a note. Use exactly one of the product ids from the account facts.', fixed)
            pid = p.get('id')
            fixed[f_p] = pid
            if pid in (st.get('promises') or dict()):
                return verdict(False, 'note_on_promise', join(['A promise is recorded for ' + p.get('label') + ', so it gets no note. Nothing was written.', wrap_up(st)]), fixed)
            if pid in (st.get('notes') or dict()):
                return verdict(False, 'note_duplicate', join(['A note for ' + p.get('label') + ' is already written. Nothing new was written.', wrap_up(st)]), fixed)
            nf = int((st.get('fails') or dict()).get(pid) or 0)
            if 0 < nf < int(prom.get('max_tool_attempts') or 2) and pid not in (st.get('retry_told') or []):
                return verdict(False, 'retry_first', join(['Recording the promise for ' + p.get('label') + ' failed, and it may be tried again. Nothing was written.',
                                                           G('promise_fail_retry', 'Call create_promise again now with exactly the same values.'),
                                                           'Write a note for it only if that fails too, or if the customer no longer wants the promise.']), fixed)
            reasons = [low(r) for r in (notes_cfg.get('reasons') or [])]
            if reasons and low(args.get(f_r)) not in reasons:
                return verdict(False, 'bad_reason', 'That reason is not allowed. Nothing was written. Use exactly one of: ' + ', '.join(notes_cfg.get('reasons')) + '.', fixed)
            return verdict(True, 'ok', '', fixed)
        # </only>

        # <only:schedule_callback>
        if tool == 'schedule_callback':
            f_d = role(tool, 'date') or 'date'
            f_t = role(tool, 'time') or 'time'
            if st.get('callback'):
                return verdict(False, 'callback_duplicate', join(['A callback is already booked. Nothing new was booked.', wrap_up(st)]), fixed)
            d = parse_date(args.get(f_d), 'dmy')
            t = parse_time(args.get(f_t)) if f_t else ''
            allowed = []
            for e in (callback_cfg.get('allowed_days') or []):
                x = eval_date(e, today)
                if x and x.isoformat() not in allowed:
                    allowed.append(x.isoformat())
            win = 'Allowed days: ' + (', '.join([fmt_date(datetime.date.fromisoformat(a)) for a in allowed]) if allowed else 'see the rules') + '.'
            lo = callback_cfg.get('earliest_time') or ''
            hi = callback_cfg.get('latest_time') or ''
            if lo or hi:
                win += ' Allowed time: from ' + (lo or '00:00') + ' to ' + (hi or '23:59') + ', both included'
                win += (', and later than now (' + now_time + ' today).') if callback_cfg.get('after_now', True) else '.'
            bad = False
            if d is None or (f_t and t is None):
                bad = True
            else:
                fixed[f_d] = wire_date(d, callback_cfg.get('wire_format'))
                if f_t:
                    fixed[f_t] = t
                if allowed and d.isoformat() not in allowed:
                    bad = True
                if f_t and lo and t < lo:
                    bad = True
                if f_t and hi and t > hi:
                    bad = True
                if callback_cfg.get('after_now', True) and (d < today or (f_t and d == today and t <= now_time)):
                    bad = True
            if bad:
                return verdict(False, 'callback_window', join(['Nothing was booked: that day or time is not allowed for a callback.', win, G('callback_outside_window', 'Follow the rule for a time outside the allowed window.')]), fixed)
            return verdict(True, 'ok', '', fixed)
        # </only>

        # <only:transfer_to_agent>
        if tool == 'transfer_to_agent':
            f_q = role(tool, 'queue') or 'queue'
            qs = [str(q.get('queue')) for q in (transfer_cfg.get('queues') or [])]
            if st.get('transfer'):
                return verdict(False, 'transfer_duplicate', join(['The call is already handed over. Nothing new was done.', wrap_up(st)]), fixed)
            if qs and low(args.get(f_q)) not in [low(q) for q in qs]:
                return verdict(False, 'bad_queue', 'That queue does not exist. Nothing was done. Use exactly one of: ' + ', '.join(qs) + '.', fixed)
            return verdict(True, 'ok', '', fixed)
        # </only>

        # <only:close_call>
        if tool == 'close_call':
            f_o = role(tool, 'outcome') or 'outcome'
            f_r = role(tool, 'reason') or 'reason'
            need = notes_needed(st)
            if need and int(st.get('close_blocks') or 0) < 2:
                return verdict(False, 'notes_missing', join(['The call was NOT closed yet.', wrap_up(st)]), fixed)
            pair = forced_close(st)
            if pair:
                fixed[f_o] = pair[0]
                fixed[f_r] = pair[1]
                return verdict(True, 'ok', '', fixed)
            outcome = str(args.get(f_o) or '')
            reason = str(args.get(f_r) or '')
            # An outcome that stands for something a tool must have done is refused when the tool did not do it.
            for rule in (closing.get('precedence') or []):
                if low(rule.get('when')) not in ('promise_recorded', 'transfer_done', 'callback_scheduled', 'identity_failed'):
                    continue
                if low(rule.get('outcome')) != low(outcome) or predicate(st, rule):
                    continue
                fixed_reason = str(rule.get('reason') or '')
                if low(rule.get('reason_source')) == 'transfer_queue' or not fixed_reason or low(fixed_reason) == low(reason):
                    if int(st.get('close_blocks') or 0) < 2:
                        what = dict(promise_recorded='a promise was recorded', transfer_done='the call was handed over with transfer_to_agent',
                                    callback_scheduled='a callback was booked', identity_failed='the identity check failed on wrong answers').get(low(rule.get('when')))
                        return verdict(False, 'outcome_without_event', 'The call was NOT closed: that outcome' + ((' with reason "' + reason + '"') if reason and fixed_reason else '') + ' is only for a call in which ' + what + ', and that did not happen. Choose the outcome and reason the rules give for what really happened and call close_call again.', fixed)
            seen, rs = table_reasons(outcome)
            if not seen:
                opts = sorted(set([str(r.get('outcome')) for r in (closing.get('table') or [])]))
                if opts and int(st.get('close_blocks') or 0) < 2:
                    return verdict(False, 'bad_outcome', 'The call was NOT closed: that outcome does not exist. Use exactly one of: ' + ', '.join(opts) + '.', fixed)
                return verdict(True, 'ok', '', fixed)
            real = [r for r in rs if r]
            if not real:
                fixed[f_r] = ''
            elif low(reason) not in [low(r) for r in real]:
                if len(real) == 1:
                    fixed[f_r] = real[0]
                elif '' in rs and not reason:
                    fixed[f_r] = ''
                elif int(st.get('close_blocks') or 0) < 2:
                    return verdict(False, 'bad_reason', 'The call was NOT closed: outcome "' + outcome + '" needs one of these reasons: ' + ', '.join(real) + '. Choose the one the rules give for what happened and call close_call again.', fixed)
            else:
                for r in real:
                    if low(r) == low(reason):
                        fixed[f_r] = r
            return verdict(True, 'ok', '', fixed)
        # </only>

        return verdict(True, 'ok', '', fixed)

    # </only>
    # ---------- hooks ----------
    # <only:pre>
    def do_pre(tool):
        if not V.get('date_facts'):
            do_setup()
        st = init_state(load_state())
        today, now_time = now_parts()
        # Measured on the platform: when the bank gives no usable answer, the agent sees a bare error and no
        # post-hook runs. The pre-hook record of that call is then still here when the next call starts.
        lost = None
        try:
            lost = json.loads(V.get('rt_pre')) if V.get('rt_pre') else None
        except Exception:
            lost = None
        if isinstance(lost, dict) and lost.get('ok') and lost.get('tool') == 'create_promise':
            lp = find_product(st, (lost.get('args') or dict()).get(role('create_promise', 'product') or 'product_id'))
            mine = None
            try:
                if tool == 'create_promise':
                    mb = _config.get('body') if isinstance(_config, dict) else None
                    if isinstance(mb, str):
                        mb = json.loads(mb)
                    mine = find_product(st, (mb or dict()).get(role('create_promise', 'product') or 'product_id'))
            except Exception:
                mine = None
            # Measured: the agent can send several promises in one message and the platform runs them at the same
            # time. A waiting promise for ANOTHER product is then not a lost call.
            if lp and mine and mine.get('id') != lp.get('id'):
                lp = None
            if lp and lp.get('id') not in (st.get('promises') or dict()):
                st['fails'][lp.get('id')] = int((st.get('fails') or dict()).get(lp.get('id')) or 0) + 1
                la = lost.get('args') or dict()
                if not isinstance(st.get('unanswered'), dict):
                    st['unanswered'] = dict()
                st['unanswered'][lp.get('id')] = dict(amount=la.get(role('create_promise', 'amount') or 'amount'), date=la.get(role('create_promise', 'date') or 'date'))
                save_state(st)
        V['rt_pre'] = ''
        body = _config.get('body') if isinstance(_config, dict) else None
        if isinstance(body, str):
            try:
                body = json.loads(body)
                _config['body'] = body
            except Exception:
                body = None
        if not isinstance(body, dict):
            return
        cv = ir.get('call_variables') or dict()
        c_field = role(tool, 'customer')
        cid = V.get(cv.get('customer_id') or 'customer_id')
        if c_field and cid:
            body[c_field] = cid
        v = judge(tool, body, st, today, now_time)
        if v.get('code') == 'retry_first':
            # said once per product: the agent may still decide that the promise is off and write the note
            st['retry_told'] = (st.get('retry_told') or []) + [body.get(role(tool, 'product') or 'product_id')]
            save_state(st)
        for k in v.get('fixed'):
            val = v.get('fixed').get(k)
            if val == '' or val is None:
                body.pop(k, None)
            else:
                body[k] = val
        try:
            V['rt_pre'] = json.dumps(dict(tool=tool, ok=v.get('ok'), code=v.get('code'), msg=v.get('msg'), args=body))
        except Exception:
            pass
        if v.get('ok'):
            if tool == 'add_note':
                np_ = find_product(st, body.get(role(tool, 'product') or 'product_id'))
                if np_:
                    V['rt_ns_' + str(np_.get('id'))] = str(body.get(role(tool, 'reason') or 'reason') or '')
                    V['rt_np'] = ''
                    V['rt_nf'] = ''
            return
        mode = low(cfg.get('block_mode'))
        if mode == 'raise':
            # Measured on the platform: an exception here stops the request, and its message is what the agent reads.
            st['blocks'] = int(st.get('blocks') or 0) + 1
            if tool == 'close_call':
                st['close_blocks'] = int(st.get('close_blocks') or 0) + 1
            save_state(st)
            V['rt_pre'] = ''
            raise RuntimeError('The call was NOT carried out. ' + str(v.get('msg')))
        if mode == 'redirect_read' and (st.get('id') or dict()).get('status') == 'verified':
            rt = cfg.get('read_tool') or 'get_account'
            _config['url'] = (api.get(rt) or dict()).get('url')
            _config['method'] = 'POST'
            nb = dict()
            rc = role(rt, 'customer')
            if rc and cid:
                nb[rc] = cid
            _config['body'] = nb
        elif mode == 'blank_url' or (mode == 'redirect_read' and cfg.get('pre_verification_block') == 'blank_url'):
            _config['url'] = ''

    # </only>
    # <only:post>
    def set_next(result, text, extra):
        d = result.get('data')
        if not isinstance(d, dict):
            d = dict()
            result['data'] = d
        d['what_to_do_next'] = text
        for k in extra:
            d[k] = extra[k]
            V[k] = extra[k]

    def do_post(tool):
        result = _result
        if not isinstance(result, dict):
            return
        result.pop('header', None)
        st = init_state(load_state())
        today, now_time = now_parts()
        pre = None
        try:
            pre = json.loads(V.get('rt_pre')) if V.get('rt_pre') else None
        except Exception:
            pre = None
        if not isinstance(pre, dict) or pre.get('tool') != tool:
            pre = None
        if pre is None:
            pre = dict(tool=tool, ok=True, code='ok', msg='', args=dict())
        V['rt_pre'] = ''
        args = pre.get('args') if isinstance(pre.get('args'), dict) else dict()
        try:
            own = _config.get('body') if isinstance(_config, dict) else None
            if isinstance(own, str):
                own = json.loads(own)
        except Exception:
            own = None
        if isinstance(own, dict) and own and low(cfg.get('block_mode')) == 'raise':
            # In this mode a refused call never gets here, so the request seen now is the one that was sent.
            args = own
            pre = dict(tool=tool, ok=True, code='ok', msg='', args=own)
            st['args_from'] = 'request'
        # A refused call counts as stopped only when the result shows it: the request was rewritten into the
        # harmless read (the product list came back), or the engine sent no request at all (no HTTP status).
        stopped = False
        if not pre.get('ok'):
            plist = (tool_ir('get_account').get('response') or dict()).get('products') or 'products'
            if plist in data_of(result):
                stopped = True
            elif result.get('status_code') is None and low(result.get('status')) != 'success':
                stopped = True
        if stopped:
            st['blocks'] = int(st.get('blocks') or 0) + 1
            if tool == 'close_call':
                st['close_blocks'] = int(st.get('close_blocks') or 0) + 1
            save_state(st)
            result['status'] = 'not_executed'
            result['message'] = 'The call was not carried out.'
            result['data'] = dict(ok=False, executed=False)
            set_next(result, pre.get('msg'), dict())
            return
        ok = call_ok(tool, result)
        d = data_of(result)
        ids = st.get('id')
        retry = G('tool_error_retry', 'The service did not answer. Nothing was done. Call the same tool again once with the same values.')

        # <only:verify_identity>
        if tool == 'verify_identity':
            qs = ident.get('questions') or []
            field = args.get(role(tool, 'field') or 'field')
            if not ok:
                msg = join(['That answer could NOT be checked and does NOT count as a wrong answer.', G('id_unreadable', 'Ask the customer to repeat the answer clearly, then check it again.')])
            else:
                hint = d.get(resp_role(tool, 'hint') or 'hint')
                hdef = None
                for h in (ident.get('hints') or []):
                    if hint and low(h.get('value')) == low(hint):
                        hdef = h
                verified = truthy(d.get(resp_role(tool, 'verified') or 'verified'))
                allv = truthy(d.get(resp_role(tool, 'all_verified') or 'all_verified'))
                left = d.get(resp_role(tool, 'wrong_left') or 'wrong_answers_left')
                if verified:
                    if not field:
                        open_qs = [x for x in qs if x.get('field') not in ids.get('passed')]
                        field = open_qs[0].get('field') if open_qs else ''
                    if field and field not in ids.get('passed'):
                        ids.get('passed').append(field)
                    pending = [x for x in qs if x.get('field') not in ids.get('passed')]
                    if allv or not pending:
                        ids['status'] = 'verified'
                        msg = G('id_all_verified', 'Identity is verified. Continue with the current task.')
                    else:
                        msg = join([G('id_correct_next', 'That answer is correct. Do not mention the account yet. Now ask the next security question:'), str(pending[0].get('ask') or pending[0].get('field'))])
                elif hdef is not None and not hdef.get('counts_as_wrong'):
                    msg = join(['That answer was not accepted, but it does NOT count as a wrong answer.', str(hdef.get('instruction') or '')])
                else:
                    ids['wrong'] = int(ids.get('wrong') or 0) + 1
                    try:
                        remaining = int(left)
                    except Exception:
                        remaining = int(ident.get('max_wrong_answers') or 3) - int(ids.get('wrong'))
                    if remaining <= 0:
                        ids['status'] = 'failed'
                        save_state(st)
                        msg = join(['The identity check has FAILED and is over. Do not ask any more security questions.', G('id_failed', 'Do not give any account information.'), wrap_up(st)])
                    else:
                        msg = join(['That answer is WRONG.', str(hdef.get('instruction') or '') if hdef else '', fill(G('id_wrong_retry', 'Tell the customer it does not match and ask the same question again. Wrong answers left: {left}.'), dict(left=remaining))])
            save_state(st)
            set_next(result, msg, dict(rt_identity=ids.get('status')))
            return
        # </only>

        # <only:create_promise>
        if tool == 'create_promise':
            p = find_product(st, args.get(role(tool, 'product') or 'product_id'))
            pid = p.get('id') if p else '?'
            label = p.get('label') if p else 'that product'
            if not ok and p:
                # Measured: a promise can be at the bank without the runtime knowing it (its answer was lost on the
                # platform side, or several calls ran at once). When the bank refuses a promise because the product
                # already has one, the bank is right: the product has its promise.
                said = low(str(d) + ' ' + str(result.get('message')))
                if 'already' in said or 'exist' in said or 'duplicate' in said:
                    ok = True
            if ok and p:
                st['fails'].pop(pid, None)
            if ok:
                if p:
                    st['promises'][pid] = dict(amount=args.get(role(tool, 'amount') or 'amount'), date=args.get(role(tool, 'date') or 'date'))
                    V['rt_p_' + str(pid)] = json.dumps(st['promises'][pid])
                    if isinstance(st.get('unanswered'), dict):
                        st['unanswered'].pop(pid, None)
                else:
                    st['promises_unknown'] = int(st.get('promises_unknown') or 0) + 1
                save_state(st)
                chain = ''
                if open_products(st):
                    chain = 'If the customer has already confirmed a promise for another product, call create_promise for it now and speak only after the last one is recorded, once for all of them.'
                msg = join(['The promise for ' + label + ' IS recorded.', chain, G('promise_ok', 'If another promise was agreed and confirmed but is not recorded yet, call create_promise for it now. Otherwise tell the customer the promise is recorded.'), wrap_up(st)])
            else:
                n = int((st.get('fails') or dict()).get(pid) or 0) + 1
                st['fails'][pid] = n
                save_state(st)
                if n < int(prom.get('max_tool_attempts') or 2):
                    msg = join(['The promise for ' + label + ' was NOT recorded: the service failed. Do NOT say it is recorded.', G('promise_fail_retry', 'Call create_promise again now with exactly the same values.')])
                else:
                    msg = join(['The promise for ' + label + ' was NOT recorded: the service failed again. Do NOT say it is recorded and do not try again.', G('promise_fail_final', 'Tell the customer you could not record it.'), wrap_up(st)])
            set_next(result, msg, dict())
            return
        # </only>

        # <only:add_note>
        if tool == 'add_note':
            p = find_product(st, args.get(role(tool, 'product') or 'product_id'))
            if ok:
                V['rt_np'] = '1'
            else:
                V['rt_nf'] = '1'
            if ok and p:
                st['notes'][p.get('id')] = str(args.get(role(tool, 'reason') or 'reason') or '')
                V['rt_n_' + str(p.get('id'))] = st['notes'][p.get('id')]
                st = init_state(st)
                save_state(st)
                msg = join(['The note for ' + p.get('label') + ' is written.', wrap_up(st)])
            else:
                msg = join(['The note was NOT written.', retry])
            set_next(result, msg, dict())
            return
        # </only>

        # <only:schedule_callback>
        if tool == 'schedule_callback':
            if ok:
                st['callback'] = str(args.get(role(tool, 'date') or 'date') or 'yes')
                save_state(st)
                msg = join(['The callback IS booked.', G('callback_ok', 'Tell the person the callback is booked.'), wrap_up(st)])
            else:
                msg = join(['The callback was NOT booked. Do not say it is booked.', retry])
            set_next(result, msg, dict())
            return
        # </only>

        # <only:transfer_to_agent>
        if tool == 'transfer_to_agent':
            if ok:
                st['transfer'] = str(args.get(role(tool, 'queue') or 'queue') or 'yes')
                save_state(st)
                msg = join(['The handover IS registered.', G('transfer_ok', 'Tell the customer you are connecting them.'), wrap_up(st)])
            else:
                msg = join(['The handover was NOT registered. Do not say they are being connected.', retry])
            set_next(result, msg, dict())
            return
        # </only>

        # <only:close_call>
        if tool == 'close_call':
            if ok:
                st['closed'] = 'yes'
                save_state(st)
                set_next(result, 'The call is closed. Do not say anything else and do not call any tool.', dict(rt_closed='yes'))
            else:
                set_next(result, 'The call was NOT closed: the service failed. Call close_call again now with the same values.', dict())
            return
        # </only>

        set_next(result, 'Continue with the current step.', dict())

    # </only>
    # ---------- code nodes ----------
    # <only:setup,plan,pre>
    def do_setup():
        st = init_state(load_state())
        today, now_time = now_parts()
        st['today'] = today.isoformat()
        st['now_time'] = now_time
        today_line = 'TODAY is ' + fmt_date(today) + '. The time now is ' + now_time + '.'
        lines = [today_line, 'CALENDAR. Use it to turn what the customer says into an exact date. Never guess a date.']
        # long enough to hold every deadline of this bank plus a week, so a later date is recognisably outside it
        days = 21
        for b in (ir.get('bands') or []):
            dl = eval_date(b.get('deadline'), today)
            if dl and (dl - today).days + 8 > days:
                days = (dl - today).days + 8
        days = min(days, int(cfg.get('calendar_days') or 45))
        for i in range(days):
            d = step(today, i)
            tag = ' = today' if i == 0 else (' = tomorrow' if i == 1 else '')
            if is_bd(d):
                lines.append(fmt_date(d) + tag + ': business day')
            else:
                why = 'bank holiday' if d.isoformat() in holidays else 'weekend'
                lines.append(fmt_date(d) + tag + ': CLOSED (' + why + '). The next business day is ' + fmt_date(next_bd(d)) + '.')
        this_sem = low(cal.get('this_weekday'))
        next_sem = low(cal.get('next_weekday'))
        if this_sem in ('current_week', 'next_occurrence') or next_sem in ('following_week', 'next_occurrence'):
            lines.append('WEEKDAY WORDS (already worked out, use them as they are):')
            first = week_first_day(today, 0)
            for i in range(7):
                d0 = step(first, i)
                name = WD[d0.weekday()]
                row = []
                if this_sem == 'current_week':
                    row.append('"this ' + name + '" = ' + d0.isoformat() + (' (already passed)' if d0 < today else ''))
                elif this_sem == 'next_occurrence':
                    dn = d0 if d0 >= today else step(d0, 7)
                    row.append('"this ' + name + '" = ' + dn.isoformat())
                if next_sem == 'following_week':
                    row.append('"next ' + name + '" = ' + step(d0, 7).isoformat())
                elif next_sem == 'next_occurrence':
                    dn = d0 if d0 > today else step(d0, 7)
                    row.append('"next ' + name + '" = ' + dn.isoformat())
                lines.append('; '.join(row))
        allowed = []
        for e in (callback_cfg.get('allowed_days') or []):
            x = eval_date(e, today)
            if x and x not in allowed:
                allowed.append(x)
        short = [today_line]
        if callback_cfg.get('used') and allowed:
            w = 'CALLBACK WINDOW: a callback may be booked only on ' + ' or '.join([fmt_date(a) for a in allowed])
            if callback_cfg.get('earliest_time') or callback_cfg.get('latest_time'):
                w += ', from ' + str(callback_cfg.get('earliest_time') or '00:00') + ' to ' + str(callback_cfg.get('latest_time') or '23:59') + ' (both included)'
            if callback_cfg.get('after_now', True):
                w += ', and it must be later than now'
            short.append(w + '. Any other day or time is outside the window.')
        V['today_facts'] = '\n'.join(short)
        V['date_facts'] = '\n'.join(lines)
        V['account_facts'] = 'No account information is available yet.'
        V['rt_identity'] = 'pending'
        V['rt_closed'] = 'no'
        V['rt_pre'] = ''
        save_state(st)

    # </only>
    # <only:plan>
    def do_plan():
        if not V.get('date_facts'):
            do_setup()
        st = init_state(load_state())
        today, now_time = now_parts()
        (st.get('id'))['status'] = 'verified'
        ps = build_products(st, today)
        if not ps:
            V['account_facts'] = join(['ACCOUNT DATA COULD NOT BE LOADED. You know no product and no amount. Do not state or guess any figure.', G('account_unavailable', 'Tell the customer you cannot access the account details right now, apologise, say goodbye and close the call.')])
            V['rt_account'] = 'unavailable'
            save_state(st)
            return
        lines = ['ACCOUNT FACTS from the bank (exact, never change a figure). There ' + ('is 1 late product.' if len(ps) == 1 else 'are ' + str(len(ps)) + ' late products, in this order.')]
        if any(p.get('deadline') for p in ps):
            lines.append('Each product lists the DATES THAT CAN BE RECORDED for it. A date the customer names can be recorded only if it is in that list. '
                         'A date that is missing from the list but earlier than its last date is a closed day: the next date in the list is the next business day. '
                         'A date later than the last date in the list is after the deadline.')
        for p in ps:
            lines.append('Product id ' + p.get('id') + ': ' + p.get('label') + '. Past-due amount (the full amount): ' + money(p.get('due')) + '. Days late: ' + str(p.get('late')) + '.')
            if p.get('floor') is not None:
                lines.append('  Lowest amount that can be recorded (the floor' + ((', ' + p.get('floor_label')) if p.get('floor_label') else '') + '): ' + money(p.get('floor')) + '. Highest: ' + money(p.get('due')) + '.')
            if p.get('deadline'):
                dl = datetime.date.fromisoformat(p.get('deadline'))
                lines.append('  Latest date that can be recorded (the deadline): ' + fmt_date(dl) + '.')
                ok_days = []
                d = today
                while d <= dl and len(ok_days) < 40:
                    if is_bd(d) or not prom.get('business_day_required', True):
                        ok_days.append(WD[d.weekday()][:3] + ' ' + str(d.day) + ' ' + MON[d.month - 1][:3] + ' (' + d.isoformat() + ')')
                    d = step(d, 1)
                lines.append('  DATES THAT CAN BE RECORDED: ' + ', '.join(ok_days) + '.')
            if p.get('band'):
                n_att = str(p.get('attempts'))
                lines.append('  Persuasion attempts allowed for this product: ' + n_att + '. One attempt is ONE proposal. After ' + n_att + ' refused proposal' + ('' if n_att == '1' else 's') + ' make no further proposal for this product.')
            for o in p.get('offers'):
                kind = o.get('amount')
                if kind == 'floor':
                    a = 'the floor ' + money(p.get('floor'))
                elif kind == 'full_or_floor_if_declined':
                    a = 'the full amount ' + money(p.get('due')) + ' (or the floor ' + money(p.get('floor')) + ' if the customer already said the full amount is too much)'
                else:
                    a = 'the full amount ' + money(p.get('due'))
                when = fmt_date(datetime.date.fromisoformat(o.get('date'))) if o.get('date') else 'the date the rules give'
                if kind == 'full_or_floor_if_declined':
                    lines.append('  Persuasion attempt ' + str(o.get('attempt')) + ' (one proposal, not two): propose the full amount ' + money(p.get('due')) + ' on ' + when
                                 + '. Only if the customer has said in words that the full amount is too much, propose the floor ' + money(p.get('floor')) + ' on that date instead.')
                else:
                    lines.append('  Persuasion attempt ' + str(o.get('attempt')) + ': propose ' + a + ' on ' + when + '.')
        V['account_facts'] = '\n'.join(lines)
        V['rt_account'] = 'loaded'
        save_state(st)

    # </only>
    if _hook == 'pre':
        do_pre(_tool)
    elif _hook == 'post':
        do_post(_tool)
    elif _hook == 'setup':
        do_setup()
    elif _hook == 'plan':
        do_plan()
