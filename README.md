# 🧠 Py-n8n — Patient Inquiry Automation

**Py-n8n** is a lightweight automation that connects **Google Forms → Google Sheets → Python**.  
It reads new form responses, summarizes them with **OpenAI**, sends email or Slack notifications,  
and marks each entry as processed in Google Sheets.

---

## ⚙️ Features
- Connects to Google Sheets via a **Google Service Account**
- Summarizes text using **OpenAI API** (optional)
- Sends email through **Gmail (App Password, SSL/465)**
- Optional **Slack notifications**
- Automatically updates and logs results in a “Processed” tab

---

## 🚀 How to Run

1. **Clone and set up environment**
   ```bash
   git clone https://github.com/<yourusername>/py-n8n.git
   cd py-n8n
   py -m venv .venv
   source .venv/Scripts/activate
   pip install -r requirements.txt

2. Create .env file

    ini
    Copy code
    EMAIL_USER=you@gmail.com
    EMAIL_PASS=your_app_password
    SPREADSHEET_ID=your_google_sheet_id
    OPENAI_API_KEY=sk-...       # optional
    
3. Add Google credentials

    Place your Google service account key as creds.json
    Share your Google Sheet with the email inside that file (client_email)

4. Submit a test form response, then run:

    bash
    python patient_automation.py

✅ Expected Output
📧 Sends summary email (or Slack message)
🗂 Marks “Processed = Yes” in the responses tab
🧾 Appends AI summary to the Processed tab

👤 Author
Shalesh Bhat