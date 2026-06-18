# Email Automation Tool

Send personalized job application emails to HR managers at scale. Each email is individually customized with the company name, role, and recipient name. Supports HTML + plain text formats, resume attachment, rate limiting, and a sent log to avoid duplicates.

## Features

- **Personalized emails** — Company name, job role, and HR name are substituted per recipient
- **Resume attachment** — Your PDF resume is attached to every email automatically
- **HTML + Plain Text** — Both formats included for best inbox delivery
- **Rate limiting** — 45-second delay between emails to avoid spam flags
- **Sent log** — Tracks every email sent; `--resume` skips already-sent companies
- **Dry run** — Preview every email before sending a single one
- **Company lists** — Drop CSV files in the `Companies/` folder; pick one at runtime
- **Single-company test** — Send to one company first to verify everything works

## Prerequisites

- Python 3.7+
- A Gmail account with 2-Step Verification enabled
- A Gmail App Password (not your regular password)

## Setup

### 1. Clone or download

```bash
git clone <your-repo-url>
cd email_automation
```

### 2. Configure your details

Open `email_config.json` and update these fields:

| Field | What to put |
|-------|-------------|
| `smtp.email` | Your Gmail address (already filled) |
| `sender.name` | Your full name |
| `sender.phone` | Your phone number |
| `sender.linkedin` | Your LinkedIn profile URL |
| `sender.resume_link` | Google Drive link to your resume (optional, for email body) |
| `settings.resume_file` | Local path to your PDF resume (already filled) |

Leave `smtp.password` as `"YOUR_APP_PASSWORD_HERE"` — you will enter it at runtime with `--prompt-password`.

### 3. Create a Gmail App Password

Google requires an App Password instead of your regular password:

1. Go to https://myaccount.google.com/apppasswords
2. If prompted, turn on **2-Step Verification** first
3. Select app: **Mail** → Device: **Other** → name it `email_automation`
4. Click **Generate**
5. Copy the 16-character code (looks like `abcd efgh ijkl mnop`)

Keep this code handy — you will paste it when the script asks.

### 4. Add companies (optional)

The `Companies/companies_v1.csv` already has 131 companies. To add your own list:

```csv
company_name,hr_email,hr_name,job_role,apply_link
Company Name,hr@company.com,Hiring Team,Python Developer,https://company.com/careers
```

Save as `Companies/my_list.csv` and use it with `--list "my_list"`.

## Usage

### Preview (see one email without sending)

```bash
python send_emails.py --preview
```

### Dry run (see all emails without sending)

```bash
python send_emails.py --dry-run
```

### Send to one company (test)

```bash
python send_emails.py --company "Infosys" --prompt-password
```

Paste your 16-character App Password when prompted.

### Send to all companies

```bash
python send_emails.py --yes --prompt-password
```

### Resume (skip already-sent companies)

```bash
python send_emails.py --resume --yes --prompt-password
```

### Use a specific company list

```bash
python send_emails.py --list "companies_v1"
python send_emails.py --list "my_list" --dry-run
```

### List available company lists

```bash
python send_emails.py --list
```

### Increase emails per session

```bash
python send_emails.py --max-emails 50 --yes --prompt-password
```

## Email Template

The current template in `email_config.json` uses these placeholders:

| Placeholder | Fills with |
|-------------|------------|
| `{hr_name}` | Recipient name from CSV |
| `{company_name}` | Company name from CSV |
| `{job_role}` | Job role from CSV |
| `{sender_phone}` | Your phone number |
| `{sender_email}` | Your email address |
| `{sender_linkedin}` | Your LinkedIn URL |

To customize the email body, edit the `templates.body_text` and `templates.body_html` fields in `email_config.json`.

## Folder Structure

```
email_automation/
├── send_emails.py              # Main automation script
├── email_config.json           # SMTP config, sender details, email templates
├── README.md                   # This file
├── Companies/
│   └── companies_v1.csv        # 131 companies with HR emails and roles
└── sent_log.csv                # Created automatically — tracks sent emails
```

## Safety Features

- **45-second delay** between sends — prevents Gmail from flagging as spam
- **Confirmation prompt** before any email is sent (skip with `--yes`)
- **Password never stored** if you use `--prompt-password`
- **Sent log** prevents double-sending with `--resume`
- **Max per session** — stops after 20 by default (override with `--max-emails`)
- **Dry run** — see exactly what will be sent without actually sending

## Customizing

### Change the email template

Edit `email_config.json` → `templates`:

- `subject` — Email subject line
- `body_html` — HTML version of the email
- `body_text` — Plain text version (used when HTML is blocked)

### Change the delay

Edit `email_config.json` → `settings.delay_seconds` or pass `--delay 60`.

### Add more companies

Create new CSV files in the `Companies/` folder and use `--list "filename"` to switch.

## Troubleshooting

| Error | Fix |
|-------|-----|
| `Application-specific password required` | You are using your regular password. Use the 16-character App Password from Google. |
| `Authentication failed` | Regenerate your App Password at https://myaccount.google.com/apppasswords |
| `Connection timed out` | Check your internet connection. Try `smtp.gmail.com` on port 587. |
| CSV not found | Run `--list` to see available company lists, then use `--list "name"`. |

## License

MIT
