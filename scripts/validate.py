#!/usr/bin/env python3
"""Offline checks for both blueprints (no API keys needed).

1. Both files are valid JSON and every module / node they reference exists.
2. The Make HTTP body becomes valid JSON for the Anthropic API once Make fills {{2.json}}.
3. The n8n request body expression builds a valid Anthropic request (run with Node.js).
4. The n8n "Parse & Score" code turns sample Claude answers into the right fields, and its
   Score matches the dashboard's calcScore (taken from the dashboard repo when it is next to this one).

Usage:  python3 scripts/validate.py
"""
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAKE = json.loads((ROOT / "blueprints/make/pvlab-gmail-lead-intake.make-blueprint.json").read_text(encoding="utf-8"))
N8N = json.loads((ROOT / "blueprints/n8n/pvlab-gmail-lead-intake.n8n-workflow.json").read_text(encoding="utf-8"))
DASHBOARD = ROOT.parent / "pvlab-leads-dashboard" / "index.html"
failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def walk_make(flow):
    for m in flow:
        yield m
        for r in m.get("routes", []):
            yield from walk_make(r["flow"])


print("Make blueprint")
modules = list(walk_make(MAKE["flow"]))
ids = [m["id"] for m in modules]
check(len(ids) == len(set(ids)), f"{len(ids)} modules with unique ids")
check(len({m['module'].split(':')[0] for m in modules}) >= 3, "connects at least 3 different apps")
text = json.dumps(MAKE)
for ref in sorted(set(int(x) for x in re.findall(r"\b(\d+)\.(?:json|data|text|subject|sender|date|is_lead|company|contact|service|mw|protocol|summary|category|confidence|fields)", text))):
    check(ref in ids, f"reference to module {ref} exists")
http = next(m for m in modules if m["module"] == "http:ActionSendData")
filled = http["mapper"]["data"].replace("{{2.json}}", json.dumps("Subject: test\n\n\"quoted\" body"))
try:
    body = json.loads(filled)
    check(body["messages"][0]["content"].startswith("Subject: test") and len(body["system"]) > 500, "HTTP body is valid Anthropic JSON once {{2.json}} is filled")
except Exception as e:  # noqa: BLE001
    check(False, f"HTTP body is valid JSON ({e})")

print("n8n workflow")
names = [n["name"] for n in N8N["nodes"]]
check(len(names) == len(set(names)), f"{len(names)} nodes with unique names")
targets = [c["node"] for src in N8N["connections"].values() for out in src["main"] for c in out]
check(all(t in names for t in targets) and all(s in names for s in N8N["connections"]), "all connections point at existing nodes")
orphans = [n for n in names if n not in targets and n != "Gmail Trigger"]
check(not orphans, f"no unconnected nodes {orphans or ''}")

node_by = {n["name"]: n for n in N8N["nodes"]}
body_expr = node_by["Claude — Classify & Extract"]["parameters"]["jsonBody"]
code_js = node_by["Parse & Score"]["parameters"]["jsCode"]
calc = "function calcScore(mw, service){return Math.min(99, Math.max(55, Math.round(52 + (mw * 0.35) + (String(service).includes('Bankability') ? 12 : 6))));}"
if DASHBOARD.exists():
    m = re.search(r"function calcScore\(mw, service\) \{[\s\S]*?\n  \}", DASHBOARD.read_text(encoding="utf-8"))
    if m:
        calc = m.group(0)
        print("  (using calcScore from the dashboard repo)")

harness = r"""
const samples = %s;
const answers = %s;
const bodyExpr = %s;
const codeJs = %s;
%s
const out = { bodies: [], parsed: [] };
samples.forEach((s, i) => {
  const $json = { subject: s.subject, from: { text: s.from }, text: s.text, date: '2026-11-23', id: 'msg' + i };
  const expr = bodyExpr.replace(/^=\{\{/, '').replace(/\}\}$/, '');
  out.bodies.push(JSON.parse(eval(expr)));
  const fn = new Function('$json', '$', codeJs);
  const res = fn({ content: [{ type: 'text', text: answers[i] }] }, () => ({ item: { json: $json } }));
  res.json.dashboardScore = calcScore(res.json.mw, res.json.service);
  out.parsed.push(res.json);
});
console.log(JSON.stringify(out));
"""
samples = json.loads((ROOT / "samples/sample-emails.json").read_text(encoding="utf-8"))
# Typical Claude answers for the samples (one wrapped in prose / fences, one broken) to exercise the parser.
answers = [
    json.dumps({"category": "lead", "is_lead": True, "confidence": 0.95, "company": "RWE Renewables Brandenburg", "contact": "Sabine Becker (s.becker@rwe.com)", "service": "EL & Flasher Testing", "mw": 32, "protocol": "Incoming-goods EL + STC flasher on sample", "summary": "Quote request for incoming inspection of 32 MWp."}),
    "```json\n" + json.dumps({"category": "lead", "is_lead": True, "confidence": 0.97, "company": "BayWa r.e.", "contact": "Thomas Lindner (t.lindner@baywa-re.com)", "service": "Bankability Due Diligence", "mw": 85, "protocol": "IEC 61215/61730 sampling", "summary": "Due diligence for 85 MW."}) + "\n```",
    json.dumps({"category": "invoice", "is_lead": False, "confidence": 0.99, "company": "", "contact": "", "service": "", "mw": 0, "protocol": "", "summary": "Supplier invoice."}),
    "not json at all",
]
script = harness % (json.dumps(samples), json.dumps(answers), json.dumps(body_expr), json.dumps(code_js), calc)
try:
    res = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
except subprocess.CalledProcessError as e:
    print(e.stderr)
    sys.exit(1)

check(all(b["model"] and b["messages"][0]["content"].startswith("Subject: ") for b in res["bodies"]), "request body expression builds valid Anthropic JSON")
p = res["parsed"]
check(p[0]["is_lead"] and p[0]["mw"] == 32 and p[0]["value"] == 16640, "lead 1 parsed: 32 MW, value 16,640 €")
check(p[1]["is_lead"] and p[1]["score"] == 94, "lead 2 parsed from fenced answer, score 94 (same as dashboard demo row)")
check(not p[2]["is_lead"] and p[2]["category"] == "invoice", "invoice routed to the non-lead branch")
check(not p[3]["is_lead"] and p[3]["category"] == "other", "unparseable answer falls back to 'other'")
check(all(x["score"] == x["dashboardScore"] for x in p), "Score equals the dashboard's calcScore for every sample")

print()
print("FAILED: %d check(s)" % len(failures) if failures else "All checks passed.")
sys.exit(1 if failures else 0)
