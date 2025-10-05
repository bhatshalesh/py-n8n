# patient_automation.py
# Py-n8n — Patient Inquiry Automation (final)
# ---------------------------------------------------------------
# ENV needed in .env (no quotes):
#   OPENAI_API_KEY=sk-...                 # optional
#   EMAIL_USER=yourgmail@gmail.com        # required for email
#   EMAIL_PASS=your_16_char_app_password  # required for email
#   SPREADSHEET_ID=1AbCDef...             # preferred
#   SPREADSHEET_NAME=Patient_Inquiries    # fallback if no ID
#   PROCESSED_SHEET=Processed
#   SLACK_BOT_TOKEN=xoxb-...              # optional
#   SLACK_CHANNEL_ID=C0123456789          # optional
#
# Place Google service account key as: creds.json
# Share your Google Sheet with creds.json's client_email (Editor).
# ---------------------------------------------------------------

import os
import sys
import json
import gspread
import pandas as pd
import yagmail
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

# ---------- helpers ----------

def die(msg: str) -> None:
    print(f"❌ {msg}")
    sys.exit(1)

def ensure_column(sheet, header_name: str) -> int:
    """
    Ensures a header named `header_name` exists in the first row of `sheet`.
    Returns the 1-based column index of that header.
    """
    headers = sheet.row_values(1)
    if header_name not in headers:
        sheet.update_cell(1, len(headers) + 1, header_name)
        headers = sheet.row_values(1)
    return headers.index(header_name) + 1  # 1-based

# ---------- 1) load env ----------

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()   # optional
EMAIL_USER      = os.getenv("EMAIL_USER", "").strip()
EMAIL_PASS      = os.getenv("EMAIL_PASS", "").strip()
SPREADSHEET_ID  = os.getenv("SPREADSHEET_ID", "").strip()  # preferred
SPREADSHEET_NAME= os.getenv("SPREADSHEET_NAME", "").strip()# fallback
PROCESSED_SHEET = os.getenv("PROCESSED_SHEET", "Processed").strip()

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "").strip() # optional
SLACK_CHANNEL_ID= os.getenv("SLACK_CHANNEL_ID", "").strip() # optional

if not (SPREADSHEET_ID or SPREADSHEET_NAME):
    die("Provide SPREADSHEET_ID (preferred) or SPREADSHEET_NAME in .env.")

if not os.path.exists("creds.json"):
    die("Missing creds.json (Google service account key) in project root.")

# ---------- 2) google auth & open workbook ----------

# Sheets scope is enough for open_by_key; Drive read-only added for name lookup.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

creds = Credentials.from_service_account_file("creds.json", scopes=SCOPES)
client = gspread.authorize(creds)

try:
    if SPREADSHEET_ID:
        wb = client.open_by_key(SPREADSHEET_ID)  # robust: avoid Drive search
    else:
        wb = client.open(SPREADSHEET_NAME)       # requires Drive RO
except Exception as e:
    die(f"Cannot open spreadsheet (ID={SPREADSHEET_ID or 'None'}, NAME='{SPREADSHEET_NAME or 'None'}'): {e}")

# First sheet = Google Form responses
sheet = wb.sheet1

# Ensure Processed sheet exists
try:
    processed_ws = wb.worksheet(PROCESSED_SHEET)
except gspread.exceptions.WorksheetNotFound:
    processed_ws = wb.add_worksheet(title=PROCESSED_SHEET, rows=200, cols=10)
    processed_ws.update("A1:F1", [["Timestamp", "Name", "Email", "Summary", "Urgency", "Status"]])

# ---------- 3) load responses & normalize headers ----------

records = sheet.get_all_records()  # row 1 as headers
df = pd.DataFrame(records)

if df.empty:
    print("ℹ️ Sheet is empty. Submit one test response in the Form, then re-run.")
    sys.exit(0)

# Ensure a 'Processed' column exists (both sheet & df)
if "Processed" not in df.columns:
    ensure_column(sheet, "Processed")
    records = sheet.get_all_records()
    df = pd.DataFrame(records)

# Case-insensitive aliases → canonical names
aliases = {
    "Timestamp": ["timestamp", "time", "date/time", "date"],
    "Name":      ["name", "full name", "your name"],
    "Email":     ["email", "e-mail", "mail", "your email"],
    "Symptoms":  [
        "symptoms", "message", "symptoms / message", "symptom details",
        "description", "issue", "problem", "notes", "details", "comments",
        "messages", "patient message"
    ],
    "Urgency":   ["urgency", "priority", "severity", "how urgent"],
}

lower_map = {c.lower().strip(): c for c in df.columns}
renames = {}
for canonical, variants in aliases.items():
    if canonical in df.columns:
        continue
    found_src = None
    for v in variants:
        src = lower_map.get(v.lower().strip())
        if src:
            found_src = src
            break
    if found_src:
        renames[found_src] = canonical

if renames:
    df = df.rename(columns=renames)

required_cols = ["Timestamp", "Name", "Email", "Symptoms", "Urgency"]
missing = [c for c in required_cols if c not in df.columns]
if missing:
    print("Columns present:", list(df.columns))
    die(f"Missing required columns after normalization: {missing}. "
        f"Rename your sheet headers or adjust Form question titles.")

# Unprocessed rows
new_rows_df = df[df.get("Processed", "") != "Yes"]
if new_rows_df.empty:
    print("✅ No new inquiries.")
    sys.exit(0)

# ---------- 4) email (SMTP) setup with SSL 465 fallback to 587 ----------

EMAIL_HOST = "smtp.gmail.com"

def connect_yagmail():
    # Prefer SSL (port 465) for stricter networks
    try:
        return yagmail.SMTP(
            EMAIL_USER,
            EMAIL_PASS,
            host=EMAIL_HOST,
            port=465,
            smtp_ssl=True,
            smtp_starttls=False,
            timeout=30,
        )
    except Exception as e1:
        print(f"⚠️ SSL 465 failed: {e1} — trying STARTTLS 587...")
        return yagmail.SMTP(
            EMAIL_USER,
            EMAIL_PASS,
            host=EMAIL_HOST,
            port=587,
            smtp_ssl=False,
            smtp_starttls=True,
            timeout=30,
        )

yag = None
if EMAIL_USER and EMAIL_PASS:
    try:
        yag = connect_yagmail()
    except Exception as e:
        print(f"⚠️ Email login failed on both 465 and 587: {e}\n"
              f"   → Will continue without sending emails.")

# ---------- 5) optional: Slack notify ----------

slack = None
if SLACK_BOT_TOKEN and SLACK_CHANNEL_ID:
    try:
        from slack_sdk.web import WebClient
        slack = WebClient(token=SLACK_BOT_TOKEN)
    except Exception as e:
        print(f"⚠️ Slack SDK not available/failed: {e}")

def slack_notify(text: str) -> bool:
    if not slack:
        return False
    try:
        slack.chat_postMessage(channel=SLACK_CHANNEL_ID, text=text)
        return True
    except Exception as e:
        print(f"⚠️ Slack send failed: {e}")
        return False

# ---------- 6) optional: OpenAI client ----------

use_openai = bool(OPENAI_API_KEY)
if use_openai:
    try:
        import openai
        openai.api_key = OPENAI_API_KEY
    except Exception as e:
        print(f"⚠️ OpenAI import/init failed, proceeding without AI: {e}")
        use_openai = False

def ai_summarize(symptoms: str, urgency: str) -> str:
    """
    Returns JSON string: {"summary": "...", "urgency": "...", "keywords": [...]}
    Falls back to simple JSON if OpenAI isn't configured.
    """
    if not use_openai:
        trunc = (symptoms or "")[:200].replace("\n", " ")
        return json.dumps({
            "summary": f"Reported symptoms: {trunc}...",
            "urgency": urgency or "Unknown",
            "keywords": []
        }, ensure_ascii=False)

    try:
        resp = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Return ONLY valid compact JSON."},
                {"role": "user", "content": (
                    "Summarize the patient's symptoms in 2 concise lines, "
                    "classify urgency as one of [Low, Medium, High], and provide 3 keywords. "
                    "Return JSON with keys: summary, urgency, keywords.\n\n"
                    f"Symptoms: {symptoms}\nReported urgency: {urgency}"
                )},
            ],
            temperature=0.2,
        )
        content = resp.choices[0].message.content.strip()
        # Try to parse JSON; if not JSON, wrap as summary text
        try:
            parsed = json.loads(content)
            out = {
                "summary": parsed.get("summary", "").strip(),
                "urgency": (parsed.get("urgency") or urgency or "Unknown").strip(),
                "keywords": parsed.get("keywords", []),
            }
            return json.dumps(out, ensure_ascii=False)
        except Exception:
            return json.dumps({
                "summary": content[:600],
                "urgency": urgency or "Unknown",
                "keywords": []
            }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "summary": f"(OpenAI error: {e})",
            "urgency": urgency or "Unknown",
            "keywords": []
        }, ensure_ascii=False)

# ---------- 7) process each unprocessed row ----------

processed_col_idx = ensure_column(sheet, "Processed")
updates_made = 0

for idx, row in new_rows_df.iterrows():
    name      = str(row.get("Name", "")).strip()
    email     = str(row.get("Email", "")).strip()
    symptoms  = str(row.get("Symptoms", "")).strip()
    urgency   = str(row.get("Urgency", "")).strip()
    timestamp = str(row.get("Timestamp", "")).strip()

    # AI JSON summary
    ai_json = ai_summarize(symptoms, urgency)

    # Email content (HTML)
    subject = f"New Patient Inquiry — {urgency or 'Unknown'} — {name or 'Unknown'}"
    body = f"""
    <h3>New Patient Inquiry</h3>
    <b>Name:</b> {name or '-'}<br>
    <b>Email:</b> {email or '-'}<br>
    <b>Reported Urgency:</b> {urgency or '-'}<br><br>
    <b>AI Summary (JSON):</b><br><pre style="white-space:pre-wrap;">{ai_json}</pre>
    <hr>
    <small>Timestamp: {timestamp or '-'}</small><br>
    <small>Raw Symptoms: {(symptoms or '-').replace('<','&lt;').replace('>','&gt;')}</small>
    """

    # Send email if possible
    if yag:
        try:
            yag.send(to=EMAIL_USER, subject=subject, contents=body)
            print(f"📧 Sent summary for {name or '(no name)'}")
        except Exception as e:
            print(f"⚠️ Email send failed for {name or '(no name)'}: {e}")
    else:
        print(f"ℹ️ Skipping email for {name or '(no name)'} (SMTP unavailable)")

    # Slack notification (optional)
    slack_notify(f"*{subject}*\n{timestamp}\n{name} <{email}>\n```{ai_json}```")

    # Mark processed in responses sheet (+2 for header and df zero-index)
    try:
        sheet.update_cell(idx + 2, processed_col_idx, "Yes")
    except Exception as e:
        print(f"⚠️ Could not mark row {idx + 2} as processed: {e}")

    # Append to Processed sheet
    try:
        parsed = json.loads(ai_json)
        summary_text = parsed.get("summary", "")
        urgency_final = parsed.get("urgency", urgency or "Unknown")
    except Exception:
        summary_text = ai_json
        urgency_final = urgency or "Unknown"

    try:
        processed_ws.append_row([timestamp, name, email, summary_text, urgency_final, "Processed"])
    except Exception as e:
        print(f"⚠️ Could not append to '{PROCESSED_SHEET}': {e}")

    updates_made += 1

print(f"✅ All new entries processed. Rows updated: {updates_made}")
