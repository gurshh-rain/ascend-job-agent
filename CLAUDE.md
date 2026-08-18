I'm building a local agentic project called "internship-bot" that searches for 2027 
summer software engineering internships, emails me a digest with Approve/Reject 
buttons for each listing, and if I approve one, appends it to a spreadsheet so I 
can apply myself later.

Stack:
- Python 3.11+
- Local LLM via Ollama (qwen2.5:7b) for parsing/classifying listings
- FastAPI for the approve/reject webhook server
- Gmail SMTP for sending emails
- Cloudflare Tunnel (or ngrok) to expose the local server for email button clicks
- CSV file as the "spreadsheet" (via pandas)
- Cron / Task Scheduler for the daily run

PROJECT STRUCTURE (already exists, do not recreate):

internship-bot/
├── config/
│   ├── settings.py          # DONE - loads .env, defines paths/constants
│   └── .env.example          # DONE - env var template
├── data/
│   ├── pending.json          # TODO
│   ├── sent_log.json        #TODO
│   └── internships.csv        #TODO
├── scraper/
│   └── scraper.py            # TODO
├── llm/
│   ├── ollama_client.py       # TODO
│   └── llm_filter.py           # TODO
├── mailer/
│   ├── templates/
│   │   └── digest_email.html   # TODO
│   └── emailer.py              # TODO
├── server/
│   └── server.py                # TODO
├── sheet/
│   └── sheet.py                  # TODO
├── run_daily.py                   # TODO
└── requirements.txt                # TODO

settings.py already exposes these constants, import from config.settings:
OLLAMA_HOST, OLLAMA_MODEL, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, 
EMAIL_FROM, EMAIL_TO, TUNNEL_BASE_URL, SERVER_PORT, INTERNSHIP_REPO_RAW_URL, 
TARGET_ROLE_KEYWORDS, TARGET_SEASON, PENDING_FILE, SENT_LOG_FILE, CSV_FILE, 
DIGEST_TEMPLATE_FILE, and a function ensure_data_files_exist().

Build the following files:

1. scraper/scraper.py
   - Fetch raw markdown from INTERNSHIP_REPO_RAW_URL (SimplifyJobs 
     Summer2027-Internships README, a markdown table of internship listings)
   - Parse the markdown table into a list of dicts: company, role, location, 
     link, date_posted (whatever fields the table actually has)
   - Return the raw list, don't filter yet (filtering happens in llm_filter.py)
   - Include a __main__ block that prints how many raw listings were found, 
     for testing

2. llm/ollama_client.py
   - A thin wrapper function `chat(prompt: str, system: str = None) -> str` 
     that POSTs to {OLLAMA_HOST}/api/chat using OLLAMA_MODEL, with 
     "format": "json" and "stream": False
   - Handle connection errors gracefully (raise a clear exception if Ollama 
     isn't running)
   - Include a __main__ test block

3. llm/llm_filter.py
   - For each raw listing from scraper.py, call ollama_client.chat() with a 
     strict prompt asking the model to:
     a) classify whether this is a genuine TARGET_SEASON software engineering 
        internship (use TARGET_ROLE_KEYWORDS), returning true/false
     b) extract/normalize fields into JSON: company, role, location, link, 
        deadline
   - Parse the model's JSON response defensively (strip markdown code fences 
     if present, handle malformed JSON without crashing the whole batch)
   - Generate a stable unique id for each listing (hash of company+role+link)
   - Return only the listings classified as relevant, as a list of dicts 
     with the id included
   - Include a __main__ test block that runs a few sample raw listings through it

4. mailer/templates/digest_email.html
   - Simple, clean HTML email template
   - Loop-friendly structure: one card/block per listing showing company, 
     role, location, deadline, and a link to the original posting
   - Each listing has two styled buttons: "Approve" linking to 
     {TUNNEL_BASE_URL}/approve?id={id} and "Reject" linking to 
     {TUNNEL_BASE_URL}/reject?id={id}
   - Use inline CSS only (email clients strip <style> blocks/external CSS)
   - Use Jinja2-style {{ }} placeholders for a listings loop

5. mailer/emailer.py
   - Function `send_digest(listings: list[dict]) -> None`
   - Render digest_email.html with Jinja2, looping over listings
   - Send via smtplib using SMTP_HOST/PORT/USER/PASSWORD from settings, 
     STARTTLS, MIMEMultipart with the HTML as an alternative part
   - Before sending, write each listing into pending.json (id -> full 
     listing dict) so the approve/reject server can look it up later
   - Also mark each id as "emailed" in sent_log.json so it's never 
     re-sent by a future scraper run
   - Include a __main__ test block with 1-2 fake sample listings

6. server/server.py
   - FastAPI app with two GET routes: /approve?id=xyz and /reject?id=xyz
   - On /approve: look up id in pending.json, if found call 
     sheet.append_listing() to write it to internships.csv, update its 
     status to "approved" in sent_log.json, remove it from pending.json, 
     return a simple HTML confirmation page ("Added ✅ - Company - Role")
   - On /reject: same lookup, update status to "rejected" in sent_log.json, 
     remove from pending.json, return a simple HTML confirmation page 
     ("Rejected")
   - Handle the case where id isn't found (already actioned or invalid) 
     with a friendly HTML message, not a raw error
   - Include a note/comment at the top of the file on how to run it: 
     uvicorn server.server:app --port {SERVER_PORT} --reload
     and how to expose it via Cloudflare Tunnel: 
     cloudflared tunnel --url http://localhost:{SERVER_PORT}

7. sheet/sheet.py
   - Function `append_listing(listing: dict) -> None` that appends a row 
     to internships.csv (id, company, role, location, link, deadline, 
     date_added, status) using pandas, creating the file with headers 
     first if needed (settings.ensure_data_files_exist() already handles 
     this, but be defensive)
   - Avoid duplicate rows if the same id is somehow appended twice

8. run_daily.py
   - Ties it together: call ensure_data_files_exist(), then scraper.py to 
     get raw listings, then llm_filter.py to filter/classify them, then 
     check sent_log.json to drop anything already emailed/approved/rejected, 
     then call emailer.send_digest() with whatever new relevant listings 
     remain
   - Print clear progress logs at each stage (X raw listings found, Y 
     passed LLM filter, Z are new, sending digest...)
   - If zero new listings, skip sending an email and just log that

9. requirements.txt
   - List all needed packages: requests, beautifulsoup4, python-dotenv, 
     fastapi, uvicorn, jinja2, pandas

Build these one at a time, test each independently before moving to the 
next (especially scraper.py and llm_filter.py, which should be testable 
without touching mailer/server at all). Ask me before making assumptions 
about the exact markdown table format in the SimplifyJobs repo, fetch and 
inspect it directly first.