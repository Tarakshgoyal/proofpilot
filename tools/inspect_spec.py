"""Inspect the PayPal Customer Disputes OpenAPI spec.

Usage: python tools/inspect_spec.py <path-to-customer_disputes_v1.json>
Prints the exact request contracts for the Day-0 lifecycle calls so the
gateway is built against the spec, not memory.
"""
import json
import sys


def deref(spec, node, depth=0):
    if not isinstance(node, dict):
        return node
    while "$ref" in node:
        ref = node["$ref"].split("/")[-1]
        node = spec["components"]["schemas"][ref]
    return node


def props(spec, schema, indent="  "):
    schema = deref(spec, schema)
    required = set(schema.get("required", []))
    out = []
    for name, p in (schema.get("properties") or {}).items():
        p = deref(spec, p)
        typ = p.get("type", "?")
        if typ == "array":
            items = deref(spec, p.get("items", {}))
            it = items.get("type", "?")
            if it == "object":
                sub = props(spec, items, indent + "    ")
                out.append(f"{indent}{name}: [{it}] REQ={name in required}")
                out.append(sub)
                continue
            if "enum" in items:
                it += f" enum={items['enum']}"
            typ = f"array<{it}>"
        if "enum" in p:
            typ += f" enum={p['enum']}"
        out.append(f"{indent}{name}: {typ} REQ={name in required}")
    return "\n".join(out)


def main(path):
    spec = json.load(open(path, encoding="utf-8"))
    paths = spec["paths"]

    targets = [
        ("list disputes", "/v1/customer/disputes", "get"),
        ("adjudicate", "/v1/customer/disputes/{id}/adjudicate", "post"),
        ("require-evidence", "/v1/customer/disputes/{id}/require-evidence", "post"),
        ("provide-evidence", "/v1/customer/disputes/{id}/provide-evidence", "post"),
        ("accept-claim", "/v1/customer/disputes/{id}/accept-claim", "post"),
        ("make-offer", "/v1/customer/disputes/{id}/make-offer", "post"),
        ("send-message", "/v1/customer/disputes/{id}/send-message", "post"),
    ]
    for label, p, method in targets:
        print(f"\n{'='*70}\n{label.upper()}  {method.upper()} {p}")
        if p not in paths or method not in paths[p]:
            print("  !! NOT FOUND IN SPEC")
            continue
        op = paths[p][method]
        if "x-sandbox-only" in json.dumps(op.get("description", "")):
            print("  (sandbox-only per description)")
        print(f"  description: {op.get('description', '')[:200]}")
        params = op.get("parameters", [])
        for par in params:
            if par.get("required"):
                print(f"  param: {par['name']} in={par.get('in')} required")
        rb = op.get("requestBody", {})
        content = rb.get("content", {})
        for ctype, c in content.items():
            print(f"  body ({ctype}):")
            if ctype.startswith("multipart"):
                for part, ps in (c.get("schema", {}).get("properties") or {}).items():
                    print(f"    part '{part}': {json.dumps(deref(spec, ps))[:300]}")
            else:
                print(props(spec, c.get("schema", {}), indent="    "))

    # Dispute resource shape
    print(f"\n{'='*70}\nDISPUTE RESOURCE")
    d = spec["components"]["schemas"].get("dispute") or spec["components"]["schemas"].get("Dispute")
    if d:
        print(props(spec, {"properties": d.get("properties", {}), "required": d.get("required", [])}))


if __name__ == "__main__":
    main(sys.argv[1])
