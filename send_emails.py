#!/usr/bin/env python3
"""
Email Automation Tool - Send customized job applications to HRs
================================================================
Reads a CSV of HR emails + company names, personalizes a template,
and sends via SMTP with rate limiting, preview, and safety checks.

Usage:
    python send_emails.py --preview              # Preview first email
    python send_emails.py --dry-run              # Preview ALL emails
    python send_emails.py                        # Send (with confirmation)
    python send_emails.py --list "companies_v1"  # Use a specific company list
    python send_emails.py --list                 # List available company lists
    python send_emails.py --company "Citi"       # Send to one company
    python send_emails.py --prompt-password      # Secure password input
"""

import argparse
import csv
import json
import logging
import os
import smtplib
import ssl
import sys
import time
from datetime import datetime
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from getpass import getpass
from pathlib import Path

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).parent
DEFAULT_CONFIG = BASE_DIR / "email_config.json"
DEFAULT_LOG = BASE_DIR / "sent_log.csv"
COMPANIES_DIR = BASE_DIR / "Companies"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mailbot")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: Path) -> dict:
    if not path.exists():
        log.error("Config file not found: %s", path)
        log.error("Copy email_config.json and fill in your credentials.")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def resolve_password(cfg: dict, prompt_pwd: bool) -> str:
    stored = cfg.get("smtp", {}).get("password", "")
    if prompt_pwd or not stored or stored == "YOUR_APP_PASSWORD_HERE":
        return getpass("Enter your email App Password (hidden input): ")
    return stored


# ---------------------------------------------------------------------------
# Template engine
# ---------------------------------------------------------------------------

def render_template(text: str, vars: dict) -> str:
    for key, val in vars.items():
        text = text.replace("{" + key + "}", str(val) if val else "")
    return text


def build_vars(company: dict, cfg: dict) -> dict:
    sender = cfg.get("sender", {})
    today = datetime.now().strftime("%B %d, %Y")
    return {
        "company_name": company.get("company_name", ""),
        "hr_name": company.get("hr_name", "Hiring Team"),
        "job_role": company.get("job_role", "Python/Django Developer"),
        "apply_link": company.get("apply_link", ""),
        "candidate_name": sender.get("name", ""),
        "candidate_email": sender.get("email", ""),
        "candidate_phone": sender.get("phone", ""),
        "candidate_degree": sender.get("degree", ""),
        "candidate_college": sender.get("college", ""),
        "passout_year": sender.get("passout_year", ""),
        "skills": sender.get("skills", ""),
        "resume_link": sender.get("resume_link", ""),
        "sender_email": sender.get("email", ""),
        "sender_phone": sender.get("phone", ""),
        "sender_linkedin": sender.get("linkedin", ""),
        "sender_name": sender.get("name", ""),
        "current_date": today,
    }


# ---------------------------------------------------------------------------
# Email composition
# ---------------------------------------------------------------------------

def build_email(company: dict, cfg: dict, vars: dict) -> MIMEMultipart:
    templates = cfg.get("templates", {})
    subject_template = templates.get("subject") or templates.get("fallback_subject", "Application - {company_name}")
    body_html_template = templates.get("body_html", "")
    body_text_template = templates.get("body_text", "")

    subject = render_template(subject_template, vars)
    html = render_template(body_html_template, vars)
    text = render_template(body_text_template, vars)

    # Use "mixed" so we can attach both body and resume file
    msg = MIMEMultipart("mixed")
    msg["From"] = cfg["smtp"]["email"]
    msg["To"] = company["hr_email"]
    msg["Subject"] = subject
    msg["Reply-To"] = cfg["smtp"]["email"]

    # Nest the text/HTML body as an "alternative" part inside "mixed"
    body_alt = MIMEMultipart("alternative")
    if text.strip():
        body_alt.attach(MIMEText(text, "plain", "utf-8"))
    if html.strip():
        body_alt.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(body_alt)

    # Attach resume PDF if configured
    resume_path = cfg.get("settings", {}).get("resume_file", "")
    if resume_path and Path(resume_path).exists():
        try:
            with open(resume_path, "rb") as f:
                attachment = MIMEBase("application", "pdf")
                attachment.set_payload(f.read())
            encoders.encode_base64(attachment)
            attachment.add_header(
                "Content-Disposition",
                "attachment",
                filename=Path(resume_path).name,
            )
            msg.attach(attachment)
        except Exception as e:
            log.warning("Could not attach resume (%s): %s", resume_path, e)
    elif resume_path:
        log.warning("Resume file not found: %s (skipping attachment)", resume_path)

    return msg


# ---------------------------------------------------------------------------
# Company list loader
# ---------------------------------------------------------------------------

def list_available_lists() -> list:
    """Return list of CSV files (without extension) in the Companies folder."""
    if not COMPANIES_DIR.exists():
        return []
    return sorted([
        f.stem for f in COMPANIES_DIR.iterdir()
        if f.suffix.lower() == ".csv"
    ])


def load_recipients(path: Path, delimiter: str = ",") -> list[dict]:
    if not path.exists():
        log.error("Company list not found: %s", path)
        sys.exit(1)

    rows = []
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        required = {"company_name", "hr_email"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            log.error("CSV must have at least columns: company_name, hr_email")
            log.error("Got columns: %s", reader.fieldnames)
            sys.exit(1)
        for row in reader:
            company = row.get("company_name", "").strip()
            email = row.get("hr_email", "").strip()
            if not company or not email:
                continue
            rows.append({
                "company_name": company,
                "hr_email": email,
                "hr_name": row.get("hr_name", "Hiring Team").strip() or "Hiring Team",
                "job_role": row.get("job_role", "Python/Django Developer").strip() or "Python/Django Developer",
                "apply_link": row.get("apply_link", "").strip(),
            })

    if not rows:
        log.error("No valid recipients found in CSV.")
        sys.exit(1)

    return rows


# ---------------------------------------------------------------------------
# Sent log
# ---------------------------------------------------------------------------

def load_sent_log(path: Path) -> set:
    if not path.exists():
        return set()
    sent = set()
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row.get("company", "").strip(), row.get("email", "").strip())
            if key:
                sent.add(key)
    return sent


def append_sent_log(path: Path, company_name: str, hr_email: str, job_role: str, status: str):
    write_header = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["timestamp", "company", "email", "role", "status"])
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            company_name,
            hr_email,
            job_role,
            status,
        ])


# ---------------------------------------------------------------------------
# SMTP connection
# ---------------------------------------------------------------------------

def connect_smtp(cfg: dict, password: str) -> smtplib.SMTP:
    smtp_cfg = cfg["smtp"]
    server = smtp_cfg["server"]
    port = smtp_cfg.get("port", 587)
    use_tls = smtp_cfg.get("use_tls", True)
    email = smtp_cfg["email"]

    log.info("Connecting to %s:%s ...", server, port)
    ctx = ssl.create_default_context()

    if use_tls:
        smtp = smtplib.SMTP(server, port, timeout=30)
        smtp.ehlo()
        smtp.starttls(context=ctx)
        smtp.ehlo()
    else:
        smtp = smtplib.SMTP_SSL(server, port, timeout=30, context=ctx)

    smtp.login(email, password)
    log.info("Connected and authenticated as %s", email)
    return smtp


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def preview_email(company: dict, cfg: dict):
    vars = build_vars(company, cfg)
    msg = build_email(company, cfg, vars)
    print("\n" + "=" * 70)
    print(f"TO:       {company['hr_email']}")
    print(f"COMPANY:  {company['company_name']}")
    print(f"HR NAME:  {vars['hr_name']}")
    print(f"ROLE:     {vars['job_role']}")
    print("-" * 70)
    print(f"SUBJECT:  {msg['Subject']}")
    print("-" * 70)
    print("BODY (Plain Text):")
    print("-" * 70)
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            print(part.get_payload(decode=True).decode("utf-8"))
            break
    print("=" * 70)
    print()


# ---------------------------------------------------------------------------
# Send single email
# ---------------------------------------------------------------------------

def send_one(smtp: smtplib.SMTP, msg: MIMEMultipart, company: dict, cfg: dict) -> bool:
    try:
        smtp.send_message(msg)
        return True
    except smtplib.SMTPRecipientsRefused as e:
        log.error("  \u2717 Recipient refused for %s (%s): %s", company["company_name"], company["hr_email"], e)
    except smtplib.SMTPSenderRefused as e:
        log.error("  \u2717 Sender refused: %s", e)
    except smtplib.SMTPDataError as e:
        log.error("  \u2717 SMTP data error: %s", e)
    except smtplib.SMTPException as e:
        log.error("  \u2717 SMTP error for %s: %s", company["company_name"], e)
    except Exception as e:
        log.error("  \u2717 Unexpected error for %s: %s", company["company_name"], e)
    return False


# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Send customized job application emails to HRs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python send_emails.py --preview
  python send_emails.py --dry-run
  python send_emails.py
  python send_emails.py --company "Citi"
  python send_emails.py --list                        # List available company lists
  python send_emails.py --list "companies_v1"         # Use a specific list
  python send_emails.py --company "Infosys" --prompt-password
  python send_emails.py --csv my_list.csv --config my_config.json
        """,
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config JSON")
    parser.add_argument("--csv", default=None, help="Path to HR list CSV (overrides --list)")
    parser.add_argument("--list", nargs="?", const="__list__", default=None,
                        help="Select a company list from Companies/ folder, or list available without value")
    parser.add_argument("--company", help="Send to a specific company only (matches company_name)")
    parser.add_argument("--delay", type=int, default=None, help="Override delay between emails (seconds)")
    parser.add_argument("--max-emails", type=int, default=None, help="Override max emails to send")
    parser.add_argument("--preview", action="store_true", help="Preview first email only (no send)")
    parser.add_argument("--dry-run", action="store_true", help="Preview ALL emails (no send)")
    parser.add_argument("--prompt-password", action="store_true", help="Prompt for SMTP password (don't use stored)")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--resume", action="store_true", help="Resume from last sent (skip already sent companies)")
    args = parser.parse_args()

    # Load config
    cfg_path = Path(args.config)
    cfg = load_config(cfg_path)

    # --- Resolve company list ---
    if args.csv:
        # Direct CSV path provided
        csv_path = Path(args.csv)
    else:
        list_name = args.list
        available = list_available_lists()

        if list_name == "__list__":
            # User just wants to see available lists
            if not available:
                log.info("No company lists found in %s.", COMPANIES_DIR)
                log.info("Place CSV files in the Companies/ folder and run again.")
                log.info("Columns required: company_name, hr_email[, hr_name, job_role, apply_link]")
            else:
                print("\nAvailable company lists in Companies/ folder:\n")
                for i, name in enumerate(available, 1):
                    print(f"  {i}. {name}.csv")
                print(f"\nUse: python send_emails.py --list \"{available[0]}\"")
            return

        if list_name:
            # Specific list requested
            csv_path = COMPANIES_DIR / f"{list_name}.csv"
            if not csv_path.exists():
                log.error("Company list '%s.csv' not found in %s.", list_name, COMPANIES_DIR)
                if available:
                    log.info("Available lists: %s", ", ".join(available))
                else:
                    log.info("Place CSV files in the Companies/ folder.")
                sys.exit(1)
        else:
            # No list specified - check if Companies folder has files
            if available:
                csv_path = COMPANIES_DIR / f"{available[0]}.csv"
                log.info("Using company list: %s (use --list to pick a different one)", csv_path.name)
            else:
                # Fall back to default CSV in base dir
                csv_path = BASE_DIR / "hr_list.csv"
                if not csv_path.exists():
                    log.error("No company lists found.")
                    log.error("Place CSV files in the Companies/ folder, or provide --csv path.")
                    sys.exit(1)

    # Load recipients
    recipients = load_recipients(csv_path, cfg.get("settings", {}).get("csv_delimiter", ","))

    # Filter by company if specified
    if args.company:
        filtered = [r for r in recipients if args.company.lower() in r["company_name"].lower()]
        if not filtered:
            log.error("No company matching '%s' found in list.", args.company)
            log.info("Available companies: %s", ", ".join(r["company_name"] for r in recipients))
            sys.exit(1)
        recipients = filtered
        log.info("Filtered to %d recipient(s) matching '%s'", len(recipients), args.company)

    # Load sent log if resuming
    sent_log_path = Path(cfg.get("settings", {}).get("sent_log_file", str(DEFAULT_LOG)))
    if args.resume:
        already_sent = load_sent_log(sent_log_path)
        before = len(recipients)
        recipients = [r for r in recipients if (r["company_name"], r["hr_email"]) not in already_sent]
        skipped = before - len(recipients)
        if skipped:
            log.info("Skipped %d already-sent companies (resume mode)", skipped)
        if not recipients:
            log.info("All companies already sent to. Nothing to do.")
            return

    # Settings
    settings = cfg.get("settings", {})
    delay = args.delay if args.delay is not None else settings.get("delay_seconds", 45)
    max_emails = args.max_emails if args.max_emails is not None else settings.get("max_emails_per_session", 999999)

    # Preview / Dry-run
    if args.preview:
        preview_email(recipients[0], cfg)
        log.info("Preview shown above. Use --dry-run to preview all, or run without flags to send.")
        return

    if args.dry_run:
        log.info("DRY RUN \u2014 showing %d email(s) without sending:\n", len(recipients))
        for i, company in enumerate(recipients, 1):
            print(f"\n--- Email {i}/{len(recipients)} ---")
            preview_email(company, cfg)
        log.info("Dry run complete. %d emails previewed.", len(recipients))
        return

    # --- SEND MODE ---
    password = resolve_password(cfg, args.prompt_password)

    # Confirmation
    total = min(len(recipients), max_emails)
    if not args.yes:
        print(f"\n  Company list:  {csv_path.name}")
        print(f"  Recipients:    {len(recipients)}")
        print(f"  Will send:     {total} (max per session)")
        print(f"  Delay:         {delay}s between emails")
        print(f"  From:          {cfg['smtp']['email']}")
        print(f"  SMTP:          {cfg['smtp']['server']}:{cfg['smtp']['port']}")
        confirm = input("\nProceed to send? (yes/NO): ").strip().lower()
        if confirm not in ("yes", "y"):
            print("Cancelled.")
            return

    # Connect
    smtp = connect_smtp(cfg, password)

    # Send loop
    sent_count = 0
    fail_count = 0
    start_time = time.time()

    try:
        for i, company in enumerate(recipients):
            if i >= max_emails:
                log.info("Reached max emails per session (%d). Stopping.", max_emails)
                break

            vars = build_vars(company, cfg)
            msg = build_email(company, cfg, vars)

            log.info("  [%d/%d] \u2192 %s (%s)", i + 1, total, company["company_name"], company["hr_email"])

            success = send_one(smtp, msg, company, cfg)

            if success:
                append_sent_log(sent_log_path, company["company_name"], company["hr_email"], vars["job_role"], "sent")
                sent_count += 1
                log.info("  \u2713 Sent to %s (%s)", company["company_name"], company["hr_email"])
            else:
                append_sent_log(sent_log_path, company["company_name"], company["hr_email"], vars["job_role"], "failed")
                fail_count += 1

            # Delay between sends (skip after last)
            if i < len(recipients) - 1 and i + 1 < max_emails:
                if delay > 0:
                    log.info("  Waiting %ds ...", delay)
                    time.sleep(delay)

    except KeyboardInterrupt:
        log.warning("\nInterrupted by user.")

    finally:
        smtp.quit()

    # Summary
    elapsed = time.time() - start_time
    print("\n" + "=" * 50)
    print(f"  SESSION COMPLETE")
    print(f"  List:     {csv_path.name}")
    print(f"  Sent:     {sent_count}")
    print(f"  Failed:   {fail_count}")
    print(f"  Time:     {elapsed:.0f}s")
    print(f"  Log:      {sent_log_path}")
    print("=" * 50)

    if fail_count == 0 and sent_count > 0:
        log.info("All emails sent successfully!")
    elif fail_count > 0:
        log.warning("%d email(s) failed. Check log for details.", fail_count)


if __name__ == "__main__":
    main()
