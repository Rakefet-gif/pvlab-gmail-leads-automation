#!/usr/bin/env python3
"""Generate the Make.com blueprint and the n8n workflow from one shared definition.

The classifier prompt lives in prompts/classifier-system-prompt.md and the AI score
formula is the same one the PV Lab dashboard uses (calcScore in index.html), so
both automations write records the dashboard shows exactly like its own.

Usage:  python3 scripts/build_blueprints.py
"""
import json
import pathlib
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROMPT = (ROOT / "prompts" / "classifier-system-prompt.md").read_text(encoding="utf-8").strip()

# ---- shared settings (placeholders are replaced after import) ----
MODEL = "claude-haiku-5-5"
AIRTABLE_BASE = "appXXXXXXXXXXXXXX"        # your Base ID, same one the dashboard uses
LEADS_TABLE = "Leads"                      # table the dashboard reads (SPEC.md section 4)
LOG_TABLE = "Inbox Log"                    # every non-lead email is logged here
SALES_EMAIL = "sales@your-domain.example"  # who gets the "new lead" alert
GMAIL_QUERY = "in:inbox -category:promotions -category:social"
VALUE_PER_MW = 520                         # same default as the dashboard: Value = MW x 520
SERVICES = [
    "EL & Flasher Testing",
    "Bankability Due Diligence",
    "On-Site Mobile PV Lab",
    "Warranty & Claim Analysis",
]
NAME = "PV Lab Germany — Gmail Lead Intake (Gmail → Claude → Airtable)"


def uid(seed: str) -> str:
    """Stable UUIDs so the file does not change on every build."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "pvlab-automation/" + seed))


# =====================================================================
# Make.com blueprint
# =====================================================================
def make_blueprint() -> dict:
    claude_body = (
        '{"model": "' + MODEL + '", "max_tokens": 800, '
        '"system": ' + json.dumps(PROMPT, ensure_ascii=False) + ', '
        '"messages": [{"role": "user", "content": {{2.json}}}]}'
    )
    score = 'min(99; max(55; round(52 + 4.mw * 0.35 + if(contains(4.service; "Bankability"); 12; 6))))'
    lead_filter = {
        "name": "Lead (is_lead = true)",
        "conditions": [[{"a": "{{4.is_lead}}", "o": "boolean:equal", "b": "true"}]],
    }
    other_filter = {
        "name": "Not a lead",
        "conditions": [[{"a": "{{4.is_lead}}", "o": "boolean:notequal", "b": "true"}]],
    }

    def designer(x, y, name):
        return {"designer": {"x": x, "y": y, "name": name}}

    flow = [
        {
            "id": 1,
            "module": "google-email:TriggerWatchEmails",
            "version": 2,
            "parameters": {
                "__IMTCONN__": None,
                "folder": "INBOX",
                "criteria": "ALL",
                "searchType": "gmail",
                "gmailFilter": GMAIL_QUERY,
                "markSeen": False,
                "maxResults": 10,
            },
            "mapper": {},
            "metadata": designer(0, 0, "1. Gmail — Watch new emails"),
        },
        {
            "id": 2,
            "module": "json:TransformToJSON",
            "version": 1,
            "parameters": {"space": ""},
            "mapper": {
                "object": "Subject: {{1.subject}}\nFrom: {{1.sender.name}} <{{1.sender.email}}>\nDate: {{1.date}}\n\n{{substring(1.text; 0; 8000)}}"
            },
            "metadata": designer(300, 0, "2. Escape email text for the API call"),
        },
        {
            "id": 3,
            "module": "http:ActionSendData",
            "version": 3,
            "parameters": {"handleErrors": True, "useNewZLibDeCompress": True},
            "mapper": {
                "url": "https://api.anthropic.com/v1/messages",
                "serializeUrl": False,
                "method": "post",
                "headers": [
                    {"name": "x-api-key", "value": "YOUR_ANTHROPIC_API_KEY"},
                    {"name": "anthropic-version", "value": "2023-06-01"},
                    {"name": "content-type", "value": "application/json"},
                ],
                "qs": [],
                "bodyType": "raw",
                "parseResponse": True,
                "authUser": "",
                "authPass": "",
                "timeout": "60",
                "shareCookies": False,
                "ca": "",
                "rejectUnauthorized": True,
                "followRedirect": True,
                "useQuerystring": False,
                "gzip": True,
                "useMtls": False,
                "contentType": "application/json",
                "data": claude_body,
                "followAllRedirects": False,
            },
            "metadata": designer(600, 0, "3. Claude — classify email & extract lead"),
        },
        {
            "id": 4,
            "module": "json:ParseJSON",
            "version": 1,
            "parameters": {"type": ""},
            "mapper": {"json": "{{3.data.content[1].text}}"},
            "metadata": designer(900, 0, "4. Parse Claude's JSON answer"),
        },
        {
            "id": 5,
            "module": "builtin:BasicRouter",
            "version": 1,
            "mapper": None,
            "metadata": designer(1200, 0, "5. Router — lead or not?"),
            "routes": [
                {
                    "flow": [
                        {
                            "id": 6,
                            "module": "airtable:ActionCreateRecord",
                            "version": 3,
                            "parameters": {"__IMTCONN__": None},
                            "filter": lead_filter,
                            "mapper": {
                                "base": AIRTABLE_BASE,
                                "table": LEADS_TABLE,
                                "typecast": True,
                                "useColumnId": False,
                                "record": {
                                    "Company": "{{4.company}}",
                                    "Contact": "{{4.contact}}",
                                    "Service": "{{4.service}}",
                                    "MW": "{{4.mw}}",
                                    "Value": "{{round(4.mw * " + str(VALUE_PER_MW) + ")}}",
                                    "Score": "{{" + score + "}}",
                                    "Status": "New",
                                    "Protocol": "{{4.protocol}}",
                                },
                            },
                            "metadata": designer(1500, -150, "6. Airtable — create lead in Leads"),
                        },
                        {
                            "id": 7,
                            "module": "google-email:ActionSendEmail",
                            "version": 2,
                            "parameters": {"__IMTCONN__": None},
                            "mapper": {
                                "to": [SALES_EMAIL],
                                "subject": "New PV lead: {{4.company}} ({{4.mw}} MW, score {{6.fields.Score}})",
                                "bodyType": "rawHtml",
                                "content": (
                                    "<p><b>{{4.company}}</b> — {{4.service}}, {{4.mw}} MW</p>"
                                    "<p>Contact: {{4.contact}}<br>AI score: {{6.fields.Score}} / 100</p>"
                                    "<p>{{4.summary}}</p>"
                                    "<p>Original email: “{{1.subject}}”</p>"
                                    "<p>The lead is now in the PV Lab dashboard with status <b>New</b>.</p>"
                                ),
                                "attachments": [],
                                "cc": [],
                                "bcc": [],
                            },
                            "metadata": designer(1800, -150, "7. Gmail — alert the sales team"),
                        },
                    ]
                },
                {
                    "flow": [
                        {
                            "id": 8,
                            "module": "airtable:ActionCreateRecord",
                            "version": 3,
                            "parameters": {"__IMTCONN__": None},
                            "filter": other_filter,
                            "mapper": {
                                "base": AIRTABLE_BASE,
                                "table": LOG_TABLE,
                                "typecast": True,
                                "useColumnId": False,
                                "record": {
                                    "Subject": "{{1.subject}}",
                                    "From": "{{1.sender.email}}",
                                    "Category": "{{4.category}}",
                                    "Confidence": "{{4.confidence}}",
                                    "Summary": "{{4.summary}}",
                                    "Received": "{{1.date}}",
                                },
                            },
                            "metadata": designer(1500, 150, "8. Airtable — log non-lead in Inbox Log"),
                        }
                    ]
                },
            ],
        },
    ]

    return {
        "name": NAME,
        "flow": flow,
        "metadata": {
            "instant": False,
            "version": 1,
            "scenario": {
                "roundtrips": 1,
                "maxErrors": 3,
                "autoCommit": True,
                "autoCommitTriggerLast": True,
                "sequential": True,
                "confidential": False,
                "dataloss": False,
                "dlq": False,
                "freshVariables": False,
            },
            "designer": {"orphans": []},
            "zone": "eu1.make.com",
        },
    }


# =====================================================================
# n8n workflow
# =====================================================================
CODE_NODE_JS = """// Parse Claude's answer and compute Score / Value exactly like the dashboard (calcScore in index.html)
const SERVICES = %s;
const raw = ($json.content && $json.content[0] && $json.content[0].text) || '';
const match = raw.match(/\\{[\\s\\S]*\\}/);
let ai;
try {
  ai = JSON.parse(match ? match[0] : raw);
} catch (e) {
  ai = { category: 'other', is_lead: false, confidence: 0, summary: 'Could not parse the model answer' };
}
const email = $('Gmail Trigger').item.json;
const isLead = ai.is_lead === true && ai.category === 'lead';
const service = SERVICES.includes(ai.service) ? ai.service : SERVICES[0];
const mw = Math.max(0, Number(ai.mw) || 0);
const score = Math.min(99, Math.max(55, Math.round(52 + (mw * 0.35) + (service.includes('Bankability') ? 12 : 6))));
return {
  json: {
    category: ai.category || 'other',
    is_lead: isLead,
    confidence: Number(ai.confidence) || 0,
    company: ai.company || '',
    contact: ai.contact || '',
    service,
    mw,
    value: Math.round(mw * %d),
    score,
    protocol: ai.protocol || '',
    summary: ai.summary || '',
    messageId: email.id,
    subject: email.subject || '',
    from: (email.from && email.from.text) || '',
    received: email.date || ''
  }
};""" % (json.dumps(SERVICES), VALUE_PER_MW)


def n8n_workflow() -> dict:
    user_content = (
        "'Subject: ' + ($json.subject || '') + '\\nFrom: ' + (($json.from && $json.from.text) || '') + "
        "'\\nDate: ' + ($json.date || '') + '\\n\\n' + String($json.text || '').slice(0, 8000)"
    )
    json_body = (
        "={{ JSON.stringify({ model: '" + MODEL + "', max_tokens: 800, system: "
        + json.dumps(PROMPT, ensure_ascii=False)
        + ", messages: [{ role: 'user', content: " + user_content + " }] }) }}"
    )

    def rl(value, mode):
        return {"__rl": True, "value": value, "mode": mode}

    nodes = [
        {
            "parameters": {
                "pollTimes": {"item": [{"mode": "everyMinute"}]},
                "simple": False,
                "filters": {"q": GMAIL_QUERY, "readStatus": "unread"},
                "options": {},
            },
            "id": uid("gmail-trigger"),
            "name": "Gmail Trigger",
            "type": "n8n-nodes-base.gmailTrigger",
            "typeVersion": 1.2,
            "position": [0, 300],
            "credentials": {"gmailOAuth2": {"id": "", "name": "Gmail account"}},
        },
        {
            "parameters": {
                "method": "POST",
                "url": "https://api.anthropic.com/v1/messages",
                "authentication": "genericCredentialType",
                "genericAuthType": "httpHeaderAuth",
                "sendHeaders": True,
                "headerParameters": {"parameters": [{"name": "anthropic-version", "value": "2023-06-01"}]},
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": json_body,
                "options": {"timeout": 60000},
            },
            "id": uid("claude"),
            "name": "Claude — Classify & Extract",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [240, 300],
            "credentials": {"httpHeaderAuth": {"id": "", "name": "Anthropic API key (x-api-key)"}},
        },
        {
            "parameters": {"mode": "runOnceForEachItem", "jsCode": CODE_NODE_JS},
            "id": uid("parse"),
            "name": "Parse & Score",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [480, 300],
        },
        {
            "parameters": {
                "conditions": {
                    "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                    "conditions": [
                        {
                            "id": uid("cond-is-lead"),
                            "leftValue": "={{ $json.is_lead }}",
                            "rightValue": "",
                            "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                        }
                    ],
                    "combinator": "and",
                },
                "options": {},
            },
            "id": uid("if"),
            "name": "Is it a lead?",
            "type": "n8n-nodes-base.if",
            "typeVersion": 2,
            "position": [720, 300],
        },
        {
            "parameters": {
                "operation": "create",
                "base": rl(AIRTABLE_BASE, "id"),
                "table": rl(LEADS_TABLE, "name"),
                "columns": {
                    "mappingMode": "defineBelow",
                    "value": {
                        "Company": "={{ $json.company }}",
                        "Contact": "={{ $json.contact }}",
                        "Service": "={{ $json.service }}",
                        "MW": "={{ $json.mw }}",
                        "Value": "={{ $json.value }}",
                        "Score": "={{ $json.score }}",
                        "Status": "New",
                        "Protocol": "={{ $json.protocol }}",
                    },
                    "matchingColumns": [],
                    "schema": [],
                },
                "options": {"typecast": True},
            },
            "id": uid("airtable-leads"),
            "name": "Airtable — Create Lead",
            "type": "n8n-nodes-base.airtable",
            "typeVersion": 2.1,
            "position": [960, 160],
            "credentials": {"airtableTokenApi": {"id": "", "name": "Airtable PAT"}},
        },
        {
            "parameters": {
                "sendTo": SALES_EMAIL,
                "subject": "=New PV lead: {{ $('Parse & Score').item.json.company }} ({{ $('Parse & Score').item.json.mw }} MW, score {{ $('Parse & Score').item.json.score }})",
                "emailType": "text",
                "message": (
                    "={{ $('Parse & Score').item.json.company }} — {{ $('Parse & Score').item.json.service }}, "
                    "{{ $('Parse & Score').item.json.mw }} MW\n"
                    "Contact: {{ $('Parse & Score').item.json.contact }}\n"
                    "AI score: {{ $('Parse & Score').item.json.score }} / 100\n\n"
                    "{{ $('Parse & Score').item.json.summary }}\n\n"
                    "Original email: {{ $('Parse & Score').item.json.subject }}\n"
                    "The lead is now in the PV Lab dashboard with status New."
                ),
                "options": {},
            },
            "id": uid("gmail-alert"),
            "name": "Gmail — Alert Sales",
            "type": "n8n-nodes-base.gmail",
            "typeVersion": 2.1,
            "position": [1200, 160],
            "credentials": {"gmailOAuth2": {"id": "", "name": "Gmail account"}},
        },
        {
            "parameters": {
                "operation": "create",
                "base": rl(AIRTABLE_BASE, "id"),
                "table": rl(LOG_TABLE, "name"),
                "columns": {
                    "mappingMode": "defineBelow",
                    "value": {
                        "Subject": "={{ $json.subject }}",
                        "From": "={{ $json.from }}",
                        "Category": "={{ $json.category }}",
                        "Confidence": "={{ $json.confidence }}",
                        "Summary": "={{ $json.summary }}",
                        "Received": "={{ $json.received }}",
                    },
                    "matchingColumns": [],
                    "schema": [],
                },
                "options": {"typecast": True},
            },
            "id": uid("airtable-log"),
            "name": "Airtable — Log Non-Lead",
            "type": "n8n-nodes-base.airtable",
            "typeVersion": 2.1,
            "position": [960, 440],
            "credentials": {"airtableTokenApi": {"id": "", "name": "Airtable PAT"}},
        },
        {
            "parameters": {"operation": "markAsRead", "messageId": "={{ $json.messageId }}"},
            "id": uid("gmail-read"),
            "name": "Gmail — Mark as Read",
            "type": "n8n-nodes-base.gmail",
            "typeVersion": 2.1,
            "position": [720, 520],
            "credentials": {"gmailOAuth2": {"id": "", "name": "Gmail account"}},
        },
    ]

    def to(*names):
        return {"main": [[{"node": n, "type": "main", "index": 0} for n in names]]}

    connections = {
        "Gmail Trigger": to("Claude — Classify & Extract"),
        "Claude — Classify & Extract": to("Parse & Score"),
        "Parse & Score": to("Is it a lead?", "Gmail — Mark as Read"),
        "Is it a lead?": {
            "main": [
                [{"node": "Airtable — Create Lead", "type": "main", "index": 0}],
                [{"node": "Airtable — Log Non-Lead", "type": "main", "index": 0}],
            ]
        },
        "Airtable — Create Lead": to("Gmail — Alert Sales"),
    }

    return {
        "name": NAME,
        "nodes": nodes,
        "connections": connections,
        "active": False,
        "settings": {"executionOrder": "v1"},
        "pinData": {},
        "meta": {"templateCredsSetupCompleted": False},
        "tags": [],
    }


# =====================================================================
# Bonus: daily quote follow-up (updates existing Airtable records)
# =====================================================================
FOLLOWUP_PROMPT = (ROOT / "prompts" / "followup-system-prompt.md").read_text(encoding="utf-8").strip()
FOLLOWUP_NAME = "PV Lab Germany — Daily Quote Follow-up (Airtable → Claude → Gmail → Airtable)"
FOLLOWUP_DAYS = 7
FOLLOWUP_MAX = 3
# Leads whose quote has been open FOLLOWUP_DAYS+ days since the status changed (or since the last
# follow-up), and that have had fewer than FOLLOWUP_MAX reminders.
FOLLOWUP_FORMULA = (
    "AND({Status} = 'Quote Sent', "
    "DATETIME_DIFF(NOW(), IF({Last Follow-up}, {Last Follow-up}, LAST_MODIFIED_TIME({Status})), 'days') >= "
    + str(FOLLOWUP_DAYS)
    + ", OR({Follow-up Count} = BLANK(), {Follow-up Count} < "
    + str(FOLLOWUP_MAX)
    + "))"
)


def followup_make_blueprint() -> dict:
    claude_body = (
        '{"model": "' + MODEL + '", "max_tokens": 600, '
        '"system": ' + json.dumps(FOLLOWUP_PROMPT, ensure_ascii=False) + ', '
        '"messages": [{"role": "user", "content": {{2.json}}}]}'
    )

    def designer(x, y, name):
        return {"designer": {"x": x, "y": y, "name": name}}

    flow = [
        {
            "id": 1,
            "module": "airtable:ActionSearchRecords",
            "version": 3,
            "parameters": {"__IMTCONN__": None},
            "mapper": {
                "base": AIRTABLE_BASE,
                "table": LEADS_TABLE,
                "formula": FOLLOWUP_FORMULA,
                "maxRecords": "50",
                "useColumnId": False,
                "sort": [],
            },
            "metadata": designer(0, 0, "1. Airtable — find quotes open 7+ days"),
        },
        {
            "id": 2,
            "module": "json:TransformToJSON",
            "version": 1,
            "parameters": {"space": ""},
            "mapper": {
                "object": (
                    "Company: {{1.Company}}\nContact: {{1.Contact}}\nService: {{1.Service}}\n"
                    "Project size: {{1.MW}} MW\nEstimated value: {{1.Value}} EUR\nAI score: {{1.Score}}\n"
                    "Protocol: {{1.Protocol}}\n"
                    "Last follow-up: {{ifempty(formatDate(1.`Last Follow-up`; \"YYYY-MM-DD\"); \"none yet\")}}\n"
                    "Follow-ups already sent: {{ifempty(1.`Follow-up Count`; 0)}}"
                )
            },
            "metadata": designer(300, 0, "2. Lead details as text"),
        },
        {
            "id": 3,
            "module": "http:ActionSendData",
            "version": 3,
            "parameters": {"handleErrors": True, "useNewZLibDeCompress": True},
            "mapper": {
                "url": "https://api.anthropic.com/v1/messages",
                "serializeUrl": False,
                "method": "post",
                "headers": [
                    {"name": "x-api-key", "value": "YOUR_ANTHROPIC_API_KEY"},
                    {"name": "anthropic-version", "value": "2023-06-01"},
                    {"name": "content-type", "value": "application/json"},
                ],
                "qs": [],
                "bodyType": "raw",
                "parseResponse": True,
                "authUser": "",
                "authPass": "",
                "timeout": "60",
                "shareCookies": False,
                "ca": "",
                "rejectUnauthorized": True,
                "followRedirect": True,
                "useQuerystring": False,
                "gzip": True,
                "useMtls": False,
                "contentType": "application/json",
                "data": claude_body,
                "followAllRedirects": False,
            },
            "metadata": designer(600, 0, "3. Claude — draft the follow-up"),
        },
        {
            "id": 4,
            "module": "google-email:ActionSendEmail",
            "version": 2,
            "parameters": {"__IMTCONN__": None},
            "mapper": {
                "to": [SALES_EMAIL],
                "subject": "Follow-up due: {{1.Company}} ({{1.MW}} MW, quote open 7+ days)",
                "bodyType": "rawHtml",
                "content": "<pre style=\"font-family:inherit;white-space:pre-wrap\">{{3.data.content[1].text}}</pre>",
                "attachments": [],
                "cc": [],
                "bcc": [],
            },
            "metadata": designer(900, 0, "4. Gmail — send reminder to sales"),
        },
        {
            "id": 5,
            "module": "airtable:ActionUpdateRecords",
            "version": 3,
            "parameters": {"__IMTCONN__": None},
            "mapper": {
                "base": AIRTABLE_BASE,
                "table": LEADS_TABLE,
                "id": "{{1.id}}",
                "typecast": True,
                "useColumnId": False,
                "record": {
                    "Last Follow-up": "{{formatDate(now; \"YYYY-MM-DD\")}}",
                    "Follow-up Count": "{{ifempty(1.`Follow-up Count`; 0) + 1}}",
                },
            },
            "metadata": designer(1200, 0, "5. Airtable — update the lead"),
        },
    ]
    return {
        "name": FOLLOWUP_NAME,
        "flow": flow,
        "metadata": {
            "instant": False,
            "version": 1,
            "scenario": {
                "roundtrips": 1,
                "maxErrors": 3,
                "autoCommit": True,
                "autoCommitTriggerLast": True,
                "sequential": True,
                "confidential": False,
                "dataloss": False,
                "dlq": False,
                "freshVariables": False,
            },
            "designer": {"orphans": []},
            "zone": "eu1.make.com",
        },
    }


FOLLOWUP_PREPARE_JS = """// Normalise the Airtable record and decide the next follow-up number
const f = $json.fields || $json;
const count = Number(f['Follow-up Count']) || 0;
const since = f['Last Follow-up'] || 'none yet';
const text = [
  'Company: ' + (f.Company || ''),
  'Contact: ' + (f.Contact || ''),
  'Service: ' + (f.Service || ''),
  'Project size: ' + (Number(f.MW) || 0) + ' MW',
  'Estimated value: ' + (Number(f.Value) || 0) + ' EUR',
  'AI score: ' + (Number(f.Score) || 0),
  'Protocol: ' + (f.Protocol || ''),
  'Last follow-up: ' + since,
  'Follow-ups already sent: ' + count
].join('\\n');
return {
  json: {
    recordId: $json.id,
    company: f.Company || '',
    mw: Number(f.MW) || 0,
    nextCount: count + 1,
    today: new Date().toISOString().slice(0, 10),
    text
  }
};"""


def followup_n8n_workflow() -> dict:
    json_body = (
        "={{ JSON.stringify({ model: '" + MODEL + "', max_tokens: 600, system: "
        + json.dumps(FOLLOWUP_PROMPT, ensure_ascii=False)
        + ", messages: [{ role: 'user', content: $json.text }] }) }}"
    )

    def rl(value, mode):
        return {"__rl": True, "value": value, "mode": mode}

    prep = "$('Prepare Lead').item.json"
    nodes = [
        {
            "parameters": {"rule": {"interval": [{"field": "cronExpression", "expression": "0 8 * * 1-5"}]}},
            "id": uid("fu-schedule"),
            "name": "Every weekday 08:00",
            "type": "n8n-nodes-base.scheduleTrigger",
            "typeVersion": 1.2,
            "position": [0, 300],
        },
        {
            "parameters": {
                "operation": "search",
                "base": rl(AIRTABLE_BASE, "id"),
                "table": rl(LEADS_TABLE, "name"),
                "filterByFormula": FOLLOWUP_FORMULA,
                "returnAll": False,
                "limit": 50,
                "options": {},
            },
            "id": uid("fu-search"),
            "name": "Airtable — Find Open Quotes",
            "type": "n8n-nodes-base.airtable",
            "typeVersion": 2.1,
            "position": [220, 300],
            "credentials": {"airtableTokenApi": {"id": "", "name": "Airtable PAT"}},
            "alwaysOutputData": False,
        },
        {
            "parameters": {"mode": "runOnceForEachItem", "jsCode": FOLLOWUP_PREPARE_JS},
            "id": uid("fu-prepare"),
            "name": "Prepare Lead",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [440, 300],
        },
        {
            "parameters": {
                "method": "POST",
                "url": "https://api.anthropic.com/v1/messages",
                "authentication": "genericCredentialType",
                "genericAuthType": "httpHeaderAuth",
                "sendHeaders": True,
                "headerParameters": {"parameters": [{"name": "anthropic-version", "value": "2023-06-01"}]},
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": json_body,
                "options": {"timeout": 60000},
            },
            "id": uid("fu-claude"),
            "name": "Claude — Draft Follow-up",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [660, 300],
            "credentials": {"httpHeaderAuth": {"id": "", "name": "Anthropic API key (x-api-key)"}},
        },
        {
            "parameters": {
                "sendTo": SALES_EMAIL,
                "subject": "=Follow-up due: {{ " + prep + ".company }} ({{ " + prep + ".mw }} MW, quote open 7+ days)",
                "emailType": "text",
                "message": "={{ $json.content[0].text }}",
                "options": {},
            },
            "id": uid("fu-gmail"),
            "name": "Gmail — Remind Sales",
            "type": "n8n-nodes-base.gmail",
            "typeVersion": 2.1,
            "position": [880, 300],
            "credentials": {"gmailOAuth2": {"id": "", "name": "Gmail account"}},
        },
        {
            "parameters": {
                "operation": "update",
                "base": rl(AIRTABLE_BASE, "id"),
                "table": rl(LEADS_TABLE, "name"),
                "columns": {
                    "mappingMode": "defineBelow",
                    "value": {
                        "id": "={{ " + prep + ".recordId }}",
                        "Last Follow-up": "={{ " + prep + ".today }}",
                        "Follow-up Count": "={{ " + prep + ".nextCount }}",
                    },
                    "matchingColumns": ["id"],
                    "schema": [],
                },
                "options": {"typecast": True},
            },
            "id": uid("fu-update"),
            "name": "Airtable — Update Lead",
            "type": "n8n-nodes-base.airtable",
            "typeVersion": 2.1,
            "position": [1100, 300],
            "credentials": {"airtableTokenApi": {"id": "", "name": "Airtable PAT"}},
        },
    ]

    def to(name):
        return {"main": [[{"node": name, "type": "main", "index": 0}]]}

    connections = {
        "Every weekday 08:00": to("Airtable — Find Open Quotes"),
        "Airtable — Find Open Quotes": to("Prepare Lead"),
        "Prepare Lead": to("Claude — Draft Follow-up"),
        "Claude — Draft Follow-up": to("Gmail — Remind Sales"),
        "Gmail — Remind Sales": to("Airtable — Update Lead"),
    }
    return {
        "name": FOLLOWUP_NAME,
        "nodes": nodes,
        "connections": connections,
        "active": False,
        "settings": {"executionOrder": "v1"},
        "pinData": {},
        "meta": {"templateCredsSetupCompleted": False},
        "tags": [],
    }


def write(path: pathlib.Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", path.relative_to(ROOT))


if __name__ == "__main__":
    write(ROOT / "blueprints" / "make" / "pvlab-gmail-lead-intake.make-blueprint.json", make_blueprint())
    write(ROOT / "blueprints" / "n8n" / "pvlab-gmail-lead-intake.n8n-workflow.json", n8n_workflow())
    write(ROOT / "blueprints" / "make" / "pvlab-quote-followup.make-blueprint.json", followup_make_blueprint())
    write(ROOT / "blueprints" / "n8n" / "pvlab-quote-followup.n8n-workflow.json", followup_n8n_workflow())
