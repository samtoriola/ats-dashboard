#!/usr/bin/env python3
"""
Pulls all issues from the APR project in Jira and writes a clean
data.json file for the ATS Project Register dashboard to consume.

Required environment variables:
  JIRA_EMAIL      - Atlassian account email
  JIRA_API_TOKEN  - Atlassian API token

Usage:
  python fetch_jira_data.py
"""

import os
import re
import json
import base64
import urllib.request
import urllib.error
from datetime import datetime, timezone

JIRA_BASE = "https://stradaeducation.atlassian.net"
PROJECT_KEY = "APR"
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "data.json")

FIELDS = [
    "summary", "issuetype", "status", "priority", "assignee", "reporter",
    "parent", "subtasks", "customfield_10015", "duedate", "created",
    "updated", "description",
]

# Visual config kept in sync with the dashboard's expectations.
WS_COLOR_MAP = {
    "State/Regional EEO Initiatives": "#378ADD",
    "Dashboard Ecosystem": "#1D9E75",
    "Ad Hoc Workstream": "#d4900a",
    "Data Infrastructure and Governance": "#7F77DD",
    "ATS Project Management": "#D4537E",
}
WS_FALLBACK_COLORS = ["#378ADD", "#1D9E75", "#d4900a", "#7F77DD", "#D4537E", "#e05a2b", "#2196a0", "#8B5CF6"]


def jira_request(jql, next_page_token=None, max_results=100):
    email = os.environ["JIRA_EMAIL"]
    token = os.environ["JIRA_API_TOKEN"]
    auth = base64.b64encode(f"{email}:{token}".encode()).decode()

    url = f"{JIRA_BASE}/rest/api/3/search/jql"
    body_dict = {
        "jql": jql,
        "maxResults": max_results,
        "fields": FIELDS,
    }
    if next_page_token:
        body_dict["nextPageToken"] = next_page_token
    body = json.dumps(body_dict).encode()

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print("Jira API error:", e.code, e.read().decode())
        raise


def fetch_all_issues():
    all_issues = []
    next_page_token = None
    while True:
        data = jira_request(f"project = {PROJECT_KEY} ORDER BY key ASC", next_page_token=next_page_token)
        issues = data.get("issues", [])
        all_issues.extend(issues)
        next_page_token = data.get("nextPageToken")
        if not next_page_token or not issues:
            break
    return all_issues


def clean_desc(adf_or_text):
    """Jira v3 API returns description as Atlassian Document Format (ADF) JSON.
    Flatten it to plain text for the dashboard tooltip/description fields."""
    if not adf_or_text:
        return ""
    if isinstance(adf_or_text, str):
        text = adf_or_text
    else:
        # Walk ADF nodes and pull out text content
        parts = []
        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "text":
                    parts.append(node.get("text", ""))
                for child in node.get("content", []):
                    walk(child)
            elif isinstance(node, list):
                for n in node:
                    walk(n)
        walk(adf_or_text)
        text = " ".join(parts)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:400]


def transform(issue):
    f = issue["fields"]
    key = issue["key"]

    itype = f["issuetype"]["name"]
    if itype == "Epic":
        itype = "Workstream"

    status = f["status"]["name"] if f.get("status") else "Prospect"
    priority = f["priority"]["name"] if f.get("priority") else "Low"
    assignee = f["assignee"]["displayName"] if f.get("assignee") else "Unassigned"
    reporter = f["reporter"]["displayName"] if f.get("reporter") else ""

    parent = f.get("parent")
    parent_summary = parent["fields"]["summary"] if parent else ""
    parent_key = parent["key"] if parent else ""

    subtasks = [st["key"] for st in f.get("subtasks", [])]

    start = f.get("customfield_10015") or ""
    due = f.get("duedate") or ""
    created = (f.get("created") or "")[:10]
    updated = (f.get("updated") or "")[:10]

    return {
        "key": key,
        "type": itype,
        "summary": f["summary"],
        "status": status,
        "parent": parent_summary,
        "parentKey": parent_key,
        "assignee": assignee,
        "priority": priority,
        "start": start,
        "due": due,
        "created": created,
        "updated": updated,
        "reporter": reporter,
        "url": f"{JIRA_BASE}/browse/{key}",
        "subtasks": subtasks,
        "desc": clean_desc(f.get("description")),
    }


def build_ws_config(issues):
    ws_issues = [i for i in issues if i["type"] == "Workstream"]
    config = []
    for idx, ws in enumerate(ws_issues):
        color = WS_COLOR_MAP.get(ws["summary"], WS_FALLBACK_COLORS[idx % len(WS_FALLBACK_COLORS)])
        config.append({"name": ws["summary"], "key": ws["key"], "color": color})
    return config


def main():
    raw_issues = fetch_all_issues()
    issues = [transform(i) for i in raw_issues]
    issues.sort(key=lambda i: int(i["key"].split("-")[1]))

    ws_config = build_ws_config(issues)
    assignees = sorted({i["assignee"] for i in issues if i["assignee"] != "Unassigned"})

    payload = {
        "issues": issues,
        "wsConfig": ws_config,
        "assignees": assignees,
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "projectKey": PROJECT_KEY,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as fh:
        json.dump(payload, fh, indent=2)

    print(f"Wrote {len(issues)} issues to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
