# Job Outreach — bilingual cold-email system for job hunting

<div align="center">

**[العربية](README.ar.md)** · English

A desktop app + CLI that sends personalised job applications from your own Gmail,
in **Arabic, English, or both in one message** — and never emails the same company twice.

</div>

---

## Why this exists

Applying to a hundred companies by hand means losing track of who you contacted,
forgetting to follow up, and getting your Gmail account flagged as spam. This
handles all three.

| Problem | How it's solved |
|---|---|
| Forgetting who you contacted | SQLite log — **sending twice to one company is impossible**, even if the run crashes midway |
| Getting flagged as spam | Randomised delays, daily cap, sending window, plus a **pre-send risk check** |
| Generic emails nobody answers | Per-company name, contact person, and a custom line — with live preview while you type |
| Forgetting follow-ups | Knows who was emailed 7+ days ago and hasn't replied; threads the reply into the same conversation |
| Dead addresses burning your reputation | **MX lookup before sending** catches addresses that will bounce |
| Not knowing who replied | Syncs with Gmail, separating real replies from bounce notifications |

Arabic is a first-class citizen: RTL templates, correct paragraph direction in
mixed-language emails, and a fully Arabic interface.

---

## Screens

The desktop app has seven sections. The one you'll live in is **Companies →
Details**, where you write the custom line for each company and watch the email
change as you type.

```
Status      → what to do next, ranked by priority
Companies   → editable table + detail drawer with live email preview
Preview     → the email exactly as the company will receive it
Send        → count, ETA, spam-risk check, confirmation, live log, stop button
Replies     → who replied, and which drafts you sent by hand
History     → every operation the system recorded
Settings    → message text and sending rhythm, no YAML editing
```

---

## Requirements

- **Windows** (the desktop app uses Edge WebView2, preinstalled on Windows 11)
- **Python 3.10+**
- A **Gmail account** and a Google Cloud project (free — setup below)

The CLI works on any OS; only the desktop app is Windows-specific.

---

## Install

```powershell
git clone https://github.com/<you>/job-outreach.git
cd job-outreach
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py init
```

`init` copies `config/*.example.yaml` into your own config files and creates the
folders. **Your config, CV, contact list, and send history are gitignored** — they
never leave your machine.

---

## Set up Gmail API (once, ~10 minutes)

1. Open [Google Cloud Console](https://console.cloud.google.com/) and sign in with
   the Gmail account you'll send from
2. Create a project
3. **APIs & Services → Library** → search **Gmail API** → **Enable**
4. **Google Auth Platform → Audience**
   - User type: **External**
   - Either add your own email under **Test users**, or **Publish** the app
   - ⚠️ In *Testing* mode Google expires your token every 7 days. Publishing avoids
     that (you'll see an "unverified app" warning you can click past)
5. **Credentials → Create Credentials → OAuth client ID → Desktop app** → download the JSON
6. Drop the file into `config/` — no renaming needed, the system recognises
   Google's default `client_secret*.json` name
7. Run:

```powershell
python main.py auth
```

---

## Fill in your details

**1. Your CV** → `attachments/CV.pdf` (change the name in `config/settings.yaml`
if yours differs)

**2. `config/profile.yaml`** — every field appears inside your emails. Anything
still marked `TODO` blocks sending. The field that matters most is
`achievement_en` / `achievement_ar`:

> ❌ "I have good experience in web development"
> ✅ "I shipped shop.com, an RTL e-commerce platform with atomic-transaction checkout, deployed via GitHub Actions"

**3. `config/settings.yaml`** — your name and sending email.

**4. Your companies** → `data/companies.csv`:

| Column | Required | Purpose |
|---|:---:|---|
| `company_name` | ✅ | Appears inside the email body |
| `email` | ✅ | Where it goes |
| `contact_name` | — | Turns "Hi there" into a name — raises reply rates noticeably |
| `role_target` | — | Role in the subject line |
| `language` | — | `ar`, `en`, or `both` (default `en`) |
| `custom_note` | — | One line specific to this company — the strongest thing you can add |
| `website` | — | Reference only |
| `attachments` | — | Override the default, `;`-separated |
| `skip` | — | `1` to exclude |

See `data/companies.example.csv`. To bulk-import, paste a list into the app, or:

```powershell
python main.py import emails.txt --language both
```

**5. Check everything:**

```powershell
python main.py validate
```

---

## Use it

**Desktop app** — double-click `تشغيل النظام.vbs`, or create a desktop shortcut
with `انشاء اختصار على سطح المكتب.vbs`.

**CLI:**

```powershell
python main.py check-domains        # find addresses that will bounce
python main.py check-domains --skip # and exclude them
python main.py dedupe               # remove duplicate emails
python main.py preview --limit 3    # render to previews/*.html

python main.py send --draft --limit 3   # safe rehearsal: Gmail drafts
python main.py send --dry-run           # show the plan only
python main.py send --limit 10          # send for real

python main.py check-replies        # who replied + resolve manual sends
python main.py followup             # follow up with non-repliers
python main.py status
```

Shared flags for `send` / `followup`: `--limit N`, `--draft`, `--dry-run`,
`--yes`, `--force`, `--ignore-schedule`.

---

## Staying out of spam

Configured under `sending` in `config/settings.yaml`:

- **Daily cap** (default 40) counted over a rolling 24 hours
- **Randomised 45–120s gap** — a *fixed* interval is itself a machine signature
- **5-minute pause** every 10 messages
- **Sending window** 8am–6pm, skipping Friday & Saturday

A **risk check runs before every send** and reports what actually matters:

| Checked | Why |
|---|---|
| Bounce rate | The single strongest spam signal — dead lists are the spammer's fingerprint |
| Domains with no MX record | Guaranteed bounces, caught before you send |
| Emails with no custom line | Near-identical mail to dozens of recipients reads as bulk |
| The same note reused | Loses its purpose for both the filter and the reader |
| Delay and daily cap | Machine rhythm gives itself away |
| Link count, trigger words, ALL CAPS | Classic filter weights |

> The check is **advisory and never blocks sending**. It also does **not** rewrite
> your text: spinning synonyms to fool a classifier is a spam technique, and
> filters detect the spinning pattern itself — the disguise becomes the evidence.

---

## Project layout

```
├── تشغيل النظام.vbs          launch the desktop app
├── app.py                    desktop app (pywebview + Edge WebView2)
├── main.py                   CLI
├── ui/index.html             app interface
├── config/
│   ├── settings.example.yaml sending rhythm, paths, sender identity
│   └── profile.example.yaml  your details — appear inside the emails
├── templates/
│   ├── ar/  en/  both/       {initial,followup}.md
├── data/companies.example.csv
├── tests/test_core.py        offline test suite
└── src/
    ├── config.py       settings loading and validation
    ├── contacts.py     CSV parsing, validation, dedup
    ├── templating.py   templates → text + HTML with correct direction
    ├── gmail_client.py OAuth, send, drafts, replies, bounces
    ├── sender.py       campaign logic, pacing, limits
    ├── spam_check.py   pre-send risk analysis
    ├── dns_check.py    MX lookups
    ├── lock.py         prevents two concurrent campaigns
    └── tracker.py      SQLite state, no-duplicate guarantee
```

---

## Templates

`templates/{ar,en,both}/{initial,followup}.md`. Format:

```
subject: "Subject line — accepts variables"
---
Body text
```

Available variables:

| Variable | Meaning |
|---|---|
| `{{ me.* }}` | Any field from `profile.yaml` |
| `{{ company_name }}` `{{ contact_name }}` `{{ role_target }}` | From the CSV |
| `{{ custom_note }}` `{{ website }}` | From the CSV |
| `{{ attachment_names }}` | List of attachment filenames |
| `{{ today }}` `{{ initial_sent_date }}` | Dates (the latter for follow-ups) |

Lines beginning `- ` become bullet lists in the HTML version, URLs become links,
and Arabic gets `dir="rtl"` automatically. Every email is sent as both plain text
and HTML in one envelope.

---

## Tests

```powershell
python -m pytest tests/ -v
```

25 tests, fully offline — no Gmail account, no network, no personal config.

---

## Security

- Credentials, token, CV, contact list, and send history are all gitignored
- Google scopes requested: `gmail.compose` (send + drafts) and `gmail.readonly`
  (reply detection). **No delete permission.**
- Revoke access any time at [myaccount.google.com/permissions](https://myaccount.google.com/permissions)

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError` | You're on system Python — activate the venv, or use `.\.venv\Scripts\python.exe main.py …` |
| `Activate.ps1 … disabled` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `access_denied 403` | Add your email under **Test users**, or publish the app |
| Token dies after 7 days | Testing mode expires tokens weekly — publish the app, or re-run `auth` |
| Arabic looks broken in the terminal | `chcp 65001` |
| App won't open | Check `data/app_error.log` |
| Run stopped midway | Just run it again — it resumes and never repeats a company |

---

## A note on responsible use

This sends **your own job applications from your own account**. It is not a bulk
mailer: the daily cap, the pacing, and the risk check exist to keep it that way.
Don't use it to email people who didn't invite contact, and don't publish scraped
contact lists — that's why `data/companies.csv` is gitignored.

## License

MIT — see [LICENSE](LICENSE).
