"""Deterministic parse of a bank tool OpenAPI file into a flat tool specification."""
import json
import pathlib

TOOL_NAMES = ["verify_identity", "get_account", "create_promise", "add_note", "schedule_callback",
              "transfer_to_agent", "close_call"]


class ApiSpecError(ValueError):
    pass


def _resolve(node, root, depth=0):
    """Follow local $ref pointers and merge allOf. Unknown shapes are returned as they are."""
    if not isinstance(node, dict) or depth > 20:
        return node if isinstance(node, dict) else {}
    if "$ref" in node:
        target = root
        for part in str(node["$ref"]).lstrip("#/").split("/"):
            target = target.get(part, {}) if isinstance(target, dict) else {}
        return _resolve(target, root, depth + 1)
    if "allOf" in node:
        merged = {k: v for k, v in node.items() if k != "allOf"}
        for sub in node["allOf"]:
            sub = _resolve(sub, root, depth + 1)
            merged.setdefault("properties", {}).update(sub.get("properties", {}))
            merged.setdefault("required", [])
            merged["required"] = list(merged["required"]) + list(sub.get("required", []))
            merged.setdefault("type", sub.get("type", "object"))
        return merged
    return node


def _fields(schema, root, depth=0):
    schema = _resolve(schema, root)
    required = set(schema.get("required") or [])
    out = []
    for name, raw in (schema.get("properties") or {}).items():
        prop = _resolve(raw, root)
        field = {"name": name, "type": prop.get("type") or "string", "required": name in required,
                 "description": prop.get("description") or ""}
        for key in ("enum", "format", "pattern", "minimum", "maximum"):
            if key in prop:
                field[key] = prop[key]
        if depth < 3:
            if field["type"] == "array":
                items = _resolve(prop.get("items") or {}, root)
                field["items_type"] = items.get("type") or "string"
                if items.get("properties"):
                    field["items"] = _fields(items, root, depth + 1)
                if "enum" in items:
                    field["items_enum"] = items["enum"]
            elif field["type"] == "object" and prop.get("properties"):
                field["fields"] = _fields(prop, root, depth + 1)
        out.append(field)
    return out


def _json_schema(content, root):
    if not isinstance(content, dict):
        return {}
    for media, body in content.items():
        if "json" in media:
            return _resolve(body.get("schema") or {}, root)
    return {}


def parse(doc):
    servers = doc.get("servers") or []
    base = (servers[0].get("url") if servers else "") or ""
    if not base.startswith("http"):
        raise ApiSpecError("the tool API file has no absolute server URL")
    tools = {}
    for path, item in (doc.get("paths") or {}).items():
        name = path.strip("/").split("/")[-1]
        for method in ("post", "put", "patch", "get"):
            op = (item or {}).get(method)
            if not op:
                continue
            own = (op.get("servers") or (item or {}).get("servers") or [{}])[0].get("url") or ""
            op_base = own if own.startswith("http") else base
            request = _fields(_json_schema((op.get("requestBody") or {}).get("content"), doc), doc)
            response = []
            for code, resp in (op.get("responses") or {}).items():
                if str(code).startswith("2"):
                    response = _fields(_json_schema(_resolve(resp, doc).get("content"), doc), doc)
                    break
            tools[name] = {"name": name, "url": op_base.rstrip("/") + "/" + path.strip("/"), "method": method.upper(),
                           "summary": " ".join(str(op.get("summary") or op.get("description") or "").split()),
                           "request": request, "response": response}
            break
    missing = [t for t in TOOL_NAMES if t not in tools]
    if missing:
        raise ApiSpecError("the tool API file does not define: " + ", ".join(missing))
    return {"title": (doc.get("info") or {}).get("title") or "", "description": (doc.get("info") or {}).get("description") or "",
            "server": base, "tools": {t: tools[t] for t in TOOL_NAMES}, "other_paths": sorted(set(tools) - set(TOOL_NAMES))}


def load(path):
    return parse(json.loads(pathlib.Path(path).read_text(encoding="utf-8")))


def field(spec, tool, name, where="request"):
    for f in spec["tools"][tool][where]:
        if f["name"] == name:
            return f
    return None


def digest(spec):
    """Compact text view of the API for model prompts."""
    lines = ["Server: " + spec["server"]]
    for t in TOOL_NAMES:
        tool = spec["tools"][t]
        lines.append(f"\n{t}: {tool['summary']}")
        for where in ("request", "response"):
            for f in tool[where]:
                bits = [f["type"]] + (["required"] if f.get("required") else [])
                if "enum" in f:
                    bits.append("enum=" + json.dumps(f["enum"]))
                if f.get("format"):
                    bits.append("format=" + f["format"])
                lines.append(f"  {where} field `{f['name']}` ({', '.join(bits)}) {f['description']}".rstrip())
                for sub in f.get("items") or f.get("fields") or []:
                    sb = [sub["type"]] + (["enum=" + json.dumps(sub["enum"])] if "enum" in sub else [])
                    lines.append(f"    item field `{sub['name']}` ({', '.join(sb)}) {sub['description']}".rstrip())
    return "\n".join(lines)
