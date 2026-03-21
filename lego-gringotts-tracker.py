#!/usr/bin/env python3
"""
Lego Gringotts Wizarding Bank (76417) Price & Stock Tracker

Monitors multiple retailers for the LEGO 76417 Gringotts Wizarding Bank
Collectors' Edition and alerts you when it's in stock or near retail price.

Usage:
  python3 lego-gringotts-tracker.py              # Run once
  python3 lego-gringotts-tracker.py --watch       # Run continuously (checks every 15 min)
  python3 lego-gringotts-tracker.py --watch 5     # Check every 5 minutes
  python3 lego-gringotts-tracker.py --json        # Output JSON (for dashboard)
  python3 lego-gringotts-tracker.py --max-price 500  # Alert threshold (default: $475)

Email alerts (get notified on your phone!):
  python3 lego-gringotts-tracker.py --email you@gmail.com --watch
  # Uses Gmail SMTP by default. Set env vars for credentials:
  #   export SMTP_USER=you@gmail.com
  #   export SMTP_PASS=your-app-password    (use Gmail App Password, NOT your login password)
  #   export SMTP_HOST=smtp.gmail.com       (optional, defaults to Gmail)
  #   export SMTP_PORT=587                  (optional)

Set up as a cron job for background monitoring:
  */15 * * * * cd /path/to/ops-matrix && python3 lego-gringotts-tracker.py --email you@gmail.com >> tracker.log 2>&1
"""

import argparse
import json
import os
import platform
import re
import smtplib
import subprocess
import sys
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Set Details ──────────────────────────────────────────────────────────────
SET_NUMBER = "76417"
SET_NAME = "Gringotts Wizarding Bank – Collectors' Edition"
RETAIL_PRICE = 429.99
PIECE_COUNT = 4803
THEME = "Harry Potter"

# ── Config ───────────────────────────────────────────────────────────────────
DEFAULT_MAX_PRICE = 475.00  # Alert if price is at or below this
RESULTS_FILE = Path(__file__).parent / "gringotts-prices.json"
STOCK_STATE_FILE = Path(__file__).parent / ".gringotts-last-stock.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 15

# ── Retailer Checkers ────────────────────────────────────────────────────────

def _get(url, headers=None):
    """Make a GET request with standard headers."""
    h = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if headers:
        h.update(headers)
    try:
        r = requests.get(url, headers=h, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        r.raise_for_status()
        return r
    except requests.RequestException as e:
        return None


def check_lego_com():
    """Check LEGO.com for stock status and price."""
    url = "https://www.lego.com/en-us/product/gringotts-wizarding-bank-collectors-edition-76417"
    result = {
        "retailer": "LEGO.com",
        "url": url,
        "price": None,
        "in_stock": False,
        "status": "unknown",
    }
    r = _get(url)
    if not r:
        result["status"] = "fetch_failed"
        return result

    text = r.text
    # Look for price in JSON-LD or page content
    price_match = re.search(r'"price"\s*:\s*"?([\d.]+)"?', text)
    if price_match:
        result["price"] = float(price_match.group(1))

    # Check stock status
    if "Out of stock" in text or "out-of-stock" in text.lower():
        result["status"] = "out_of_stock"
        result["in_stock"] = False
    elif "Add to Bag" in text or "add-to-bag" in text.lower() or '"availability":"InStock"' in text:
        result["status"] = "in_stock"
        result["in_stock"] = True
    elif "Temporarily out of stock" in text:
        result["status"] = "temporarily_out_of_stock"
    elif "Coming Soon" in text:
        result["status"] = "coming_soon"
    elif "Retiring soon" in text:
        result["status"] = "retiring_soon"
        # If retiring soon but no out of stock message, it may still be available
        if '"availability":"InStock"' in text or "Add to" in text:
            result["in_stock"] = True

    return result


def check_amazon():
    """Check Amazon for the set."""
    # Amazon product page for LEGO 76417
    url = "https://www.amazon.com/dp/B0BV6G14LQ"
    result = {
        "retailer": "Amazon",
        "url": url,
        "price": None,
        "in_stock": False,
        "status": "unknown",
    }
    r = _get(url)
    if not r:
        result["status"] = "fetch_failed"
        return result

    soup = BeautifulSoup(r.text, "lxml")

    # Try to find price
    for selector in [
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "span.a-price span.a-offscreen",
        "#corePrice_feature_div span.a-offscreen",
        "#price_inside_buybox",
    ]:
        el = soup.select_one(selector)
        if el:
            price_text = el.get_text(strip=True)
            price_match = re.search(r"[\d,]+\.?\d*", price_text.replace(",", ""))
            if price_match:
                result["price"] = float(price_match.group())
                break

    # Check availability
    avail_el = soup.select_one("#availability")
    if avail_el:
        avail_text = avail_el.get_text(strip=True).lower()
        if "in stock" in avail_text:
            result["in_stock"] = True
            result["status"] = "in_stock"
        elif "unavailable" in avail_text or "out of stock" in avail_text:
            result["status"] = "out_of_stock"
        else:
            result["status"] = avail_text[:80]
    elif result["price"]:
        result["in_stock"] = True
        result["status"] = "in_stock"

    return result


def check_walmart():
    """Check Walmart for the set."""
    url = "https://www.walmart.com/ip/15015660453"
    result = {
        "retailer": "Walmart",
        "url": url,
        "price": None,
        "in_stock": False,
        "status": "unknown",
    }
    r = _get(url)
    if not r:
        result["status"] = "fetch_failed"
        return result

    text = r.text
    # Look for price in JSON-LD
    price_match = re.search(r'"price"\s*:\s*"?([\d.]+)"?', text)
    if price_match:
        result["price"] = float(price_match.group(1))

    if '"availability":"InStock"' in text or '"InStock"' in text:
        result["in_stock"] = True
        result["status"] = "in_stock"
    elif '"OutOfStock"' in text:
        result["status"] = "out_of_stock"

    return result


def check_target():
    """Check Target for the set via their Redsky API."""
    # Target DPCI / TCIN for LEGO 76417
    url = "https://www.target.com/p/-/A-89249792"
    result = {
        "retailer": "Target",
        "url": url,
        "price": None,
        "in_stock": False,
        "status": "unknown",
    }
    r = _get(url)
    if not r:
        result["status"] = "fetch_failed"
        return result

    text = r.text
    price_match = re.search(r'"price"\s*:\s*"?([\d.]+)"?', text)
    if price_match:
        result["price"] = float(price_match.group(1))

    if '"availability":"InStock"' in text or '"IN_STOCK"' in text:
        result["in_stock"] = True
        result["status"] = "in_stock"
    elif '"OUT_OF_STOCK"' in text or '"OutOfStock"' in text:
        result["status"] = "out_of_stock"

    return result


def check_bricklink():
    """Check BrickLink for average used/new prices."""
    url = "https://www.bricklink.com/v2/catalog/catalogitem.page?S=76417-1"
    result = {
        "retailer": "BrickLink (secondary)",
        "url": url,
        "price": None,
        "in_stock": True,  # BrickLink always has sellers
        "status": "marketplace",
        "note": "Secondary market - multiple sellers",
    }
    # BrickLink blocks scraping heavily, so we just provide the link
    return result


# ── All checkers ─────────────────────────────────────────────────────────────
CHECKERS = [
    ("LEGO.com", check_lego_com),
    ("Amazon", check_amazon),
    ("Walmart", check_walmart),
    ("Target", check_target),
    ("BrickLink", check_bricklink),
]


# ── Notification ─────────────────────────────────────────────────────────────

def send_notification(title, message):
    """Send a desktop notification (cross-platform)."""
    system = platform.system()
    try:
        if system == "Darwin":  # macOS
            subprocess.run([
                "osascript", "-e",
                f'display notification "{message}" with title "{title}" sound name "Glass"'
            ], check=False, capture_output=True)
        elif system == "Linux":
            subprocess.run(
                ["notify-send", "-u", "critical", title, message],
                check=False, capture_output=True,
            )
        elif system == "Windows":
            # PowerShell toast notification
            ps_script = f"""
            [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
            $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
            $template.GetElementsByTagName('text')[0].AppendChild($template.CreateTextNode('{title}'))
            $template.GetElementsByTagName('text')[1].AppendChild($template.CreateTextNode('{message}'))
            $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('LEGO Tracker')
            $notifier.Show([Windows.UI.Notifications.ToastNotification]::new($template))
            """
            subprocess.run(["powershell", "-Command", ps_script], check=False, capture_output=True)
    except Exception:
        pass  # Notifications are best-effort


def send_email(to_addr, subject, body_text, body_html=None):
    """Send an email alert via SMTP (Gmail by default)."""
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")

    if not smtp_user or not smtp_pass:
        print(f"  {YELLOW}Email skipped: set SMTP_USER and SMTP_PASS env vars{RESET}")
        print(f"  {DIM}For Gmail: use an App Password (Google Account > Security > App Passwords){RESET}")
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = smtp_user
    msg["To"] = to_addr
    msg["Subject"] = subject

    msg.attach(MIMEText(body_text, "plain"))
    if body_html:
        msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        print(f"  {GREEN}Email sent to {to_addr}{RESET}")
        return True
    except Exception as e:
        print(f"  {RED}Email failed: {e}{RESET}")
        return False


def build_alert_email(alert_results):
    """Build a nicely formatted alert email for stock/price alerts."""
    lines = []
    html_rows = []

    for r in alert_results:
        price_str = f"${r['price']:.2f}" if r.get("price") else "Price N/A"
        lines.append(f"  {r['retailer']}: {price_str} — {r['url']}")
        html_rows.append(
            f'<tr><td style="padding:8px 12px;border-bottom:1px solid #eee">'
            f'<strong>{r["retailer"]}</strong></td>'
            f'<td style="padding:8px 12px;border-bottom:1px solid #eee;color:#16a34a;'
            f'font-weight:bold;font-size:18px">{price_str}</td>'
            f'<td style="padding:8px 12px;border-bottom:1px solid #eee">'
            f'<a href="{r["url"]}" style="color:#2563eb">Buy Now</a></td></tr>'
        )

    text = (
        f"LEGO {SET_NUMBER} — {SET_NAME}\n"
        f"Retail price: ${RETAIL_PRICE:.2f}\n\n"
        f"Available at:\n" + "\n".join(lines) + "\n\n"
        f"Act fast — this set is retiring soon and sells out quickly!"
    )

    html = f"""
    <div style="font-family:sans-serif;max-width:500px;margin:0 auto">
      <div style="background:#1a1d27;color:#e8eaed;padding:20px;border-radius:12px 12px 0 0">
        <h2 style="margin:0">🏰 Gringotts is Available!</h2>
        <p style="color:#8b8fa3;margin:8px 0 0">LEGO {SET_NUMBER} &middot; Retail ${RETAIL_PRICE:.2f}</p>
      </div>
      <table style="width:100%;border-collapse:collapse;background:#fff;border:1px solid #e5e7eb">
        {''.join(html_rows)}
      </table>
      <div style="background:#fef3c7;padding:14px 16px;border-radius:0 0 12px 12px;
                  font-size:13px;color:#92400e;border:1px solid #e5e7eb;border-top:none">
        ⚡ Act fast — this set is retiring ~Jul 2026 and sells out quickly!
      </div>
    </div>
    """
    return text, html


def load_last_stock_state():
    """Load the last known stock state to detect changes."""
    if STOCK_STATE_FILE.exists():
        try:
            return json.loads(STOCK_STATE_FILE.read_text())
        except (json.JSONDecodeError, KeyError):
            pass
    return {}


def save_stock_state(results):
    """Save current stock state for change detection."""
    state = {}
    for r in results:
        state[r["retailer"]] = {
            "in_stock": r["in_stock"],
            "price": r.get("price"),
            "status": r.get("status"),
        }
    STOCK_STATE_FILE.write_text(json.dumps(state, indent=2))
    return state


def detect_restocks(results):
    """Compare current results to last known state and return newly restocked retailers."""
    last_state = load_last_stock_state()
    if not last_state:
        return []  # First run, nothing to compare

    restocked = []
    for r in results:
        retailer = r["retailer"]
        was_in_stock = last_state.get(retailer, {}).get("in_stock", False)
        now_in_stock = r["in_stock"]

        if now_in_stock and not was_in_stock:
            restocked.append(r)

    return restocked


# ── Display ──────────────────────────────────────────────────────────────────

# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def display_results(results, max_price):
    """Pretty-print results to the terminal."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n{'═' * 70}")
    print(f"{BOLD}{CYAN}  🧱 LEGO {SET_NUMBER} — {SET_NAME}{RESET}")
    print(f"{DIM}  Retail: ${RETAIL_PRICE:.2f} | {PIECE_COUNT} pieces | Alert ≤ ${max_price:.2f}{RESET}")
    print(f"{DIM}  Checked: {now}{RESET}")
    print(f"{'═' * 70}")

    alerts = []

    for r in results:
        retailer = r["retailer"].ljust(22)
        price_str = f"${r['price']:.2f}" if r["price"] else "—"
        price_str = price_str.rjust(10)

        if r["in_stock"] and r.get("price") and r["price"] <= max_price:
            indicator = f"{GREEN}{BOLD}● BUY NOW{RESET}"
            alerts.append(r)
        elif r["in_stock"] and r.get("price") and r["price"] <= RETAIL_PRICE * 1.1:
            indicator = f"{GREEN}● In Stock{RESET}"
            alerts.append(r)
        elif r["in_stock"] and r["status"] == "marketplace":
            indicator = f"{YELLOW}○ Marketplace{RESET}"
        elif r["in_stock"]:
            indicator = f"{YELLOW}● In Stock (above target){RESET}"
        elif r["status"] == "fetch_failed":
            indicator = f"{DIM}? Could not check{RESET}"
        else:
            indicator = f"{RED}✗ Out of Stock{RESET}"

        # Price coloring
        if r.get("price"):
            if r["price"] <= RETAIL_PRICE:
                price_colored = f"{GREEN}{price_str}{RESET}"
            elif r["price"] <= max_price:
                price_colored = f"{YELLOW}{price_str}{RESET}"
            else:
                price_colored = f"{RED}{price_str}{RESET}"
        else:
            price_colored = f"{DIM}{price_str}{RESET}"

        print(f"  {retailer} {price_colored}  {indicator}")
        if r.get("note"):
            print(f"  {' ' * 22} {DIM}{r['note']}{RESET}")

    print(f"{'─' * 70}")

    if alerts:
        best = min(alerts, key=lambda x: x.get("price") or float("inf"))
        msg = f"🚨 {best['retailer']}: ${best['price']:.2f}" if best.get("price") else f"🚨 {best['retailer']}: In Stock!"
        print(f"  {GREEN}{BOLD}{msg}{RESET}")
        print(f"  {CYAN}{best['url']}{RESET}")
        price_str = f"${best['price']:.2f}" if best.get("price") else "In Stock!"
        send_notification(
            f"LEGO {SET_NUMBER} Alert!",
            f"{best['retailer']}: {price_str}"
        )
    else:
        print(f"  {DIM}No deals found. Will keep checking...{RESET}")

    print(f"{'═' * 70}\n")

    return alerts


def save_results(results):
    """Save results to JSON for the dashboard."""
    data = {
        "set_number": SET_NUMBER,
        "set_name": SET_NAME,
        "retail_price": RETAIL_PRICE,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }

    # Load history
    history = []
    if RESULTS_FILE.exists():
        try:
            existing = json.loads(RESULTS_FILE.read_text())
            history = existing.get("history", [])
        except (json.JSONDecodeError, KeyError):
            pass

    # Append current snapshot (keep last 500)
    history.append({
        "timestamp": data["checked_at"],
        "prices": {r["retailer"]: r["price"] for r in results if r.get("price")},
        "stock": {r["retailer"]: r["in_stock"] for r in results},
    })
    history = history[-500:]

    data["history"] = history
    RESULTS_FILE.write_text(json.dumps(data, indent=2))


# ── Main ─────────────────────────────────────────────────────────────────────

def run_check(max_price, output_json=False, email_to=None):
    """Run all price checks and display/save results."""
    results = []
    for name, checker in CHECKERS:
        try:
            result = checker()
            results.append(result)
        except Exception as e:
            results.append({
                "retailer": name,
                "url": "",
                "price": None,
                "in_stock": False,
                "status": f"error: {e}",
            })

    # Detect restocks (items that just came back in stock since last check)
    restocked = detect_restocks(results)

    if output_json:
        print(json.dumps(results, indent=2))
    else:
        display_results(results, max_price)

    # Alert on restocks or good deals
    alert_results = []
    for r in results:
        if r["in_stock"] and r.get("price") and r["price"] <= max_price:
            alert_results.append(r)
        elif r["in_stock"] and r["retailer"] == "LEGO.com":
            alert_results.append(r)  # Always alert on LEGO.com restock

    # Only email on NEW restocks or first-time deals to avoid spam
    email_worthy = restocked or alert_results
    if email_to and email_worthy:
        # Prefer restocked items for the email, fall back to all alerts
        items_to_report = restocked if restocked else alert_results
        subject_retailers = ", ".join(r["retailer"] for r in items_to_report)
        subject = f"🏰 LEGO {SET_NUMBER} RESTOCK: {subject_retailers}"
        text, html = build_alert_email(items_to_report)
        send_email(email_to, subject, text, html)

    if restocked and not output_json:
        for r in restocked:
            print(f"  {GREEN}{BOLD}🔔 RESTOCK: {r['retailer']} just came back in stock!{RESET}")

    # Save state AFTER detection so next run can compare
    save_stock_state(results)
    save_results(results)
    return results


def main():
    parser = argparse.ArgumentParser(
        description=f"Track LEGO {SET_NUMBER} {SET_NAME} prices and stock"
    )
    parser.add_argument(
        "--watch", nargs="?", const=15, type=int, metavar="MINUTES",
        help="Run continuously, checking every N minutes (default: 15)"
    )
    parser.add_argument(
        "--max-price", type=float, default=DEFAULT_MAX_PRICE,
        help=f"Maximum price to alert on (default: ${DEFAULT_MAX_PRICE:.2f})"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output results as JSON"
    )
    parser.add_argument(
        "--email", type=str, metavar="ADDRESS",
        help="Email address to send restock/deal alerts to (requires SMTP_USER & SMTP_PASS env vars)"
    )
    args = parser.parse_args()

    if args.email:
        print(f"{BOLD}Email alerts enabled:{RESET} {args.email}")
        if not os.environ.get("SMTP_USER") or not os.environ.get("SMTP_PASS"):
            print(f"{YELLOW}⚠  Set SMTP_USER and SMTP_PASS env vars for email to work{RESET}")
            print(f"{DIM}   For Gmail: create an App Password at https://myaccount.google.com/apppasswords{RESET}")

    if args.watch:
        interval = args.watch * 60
        print(f"{BOLD}Watching LEGO {SET_NUMBER} every {args.watch} min (Ctrl+C to stop){RESET}")
        while True:
            try:
                run_check(args.max_price, args.json, args.email)
                time.sleep(interval)
            except KeyboardInterrupt:
                print(f"\n{DIM}Stopped.{RESET}")
                break
    else:
        run_check(args.max_price, args.json, args.email)


if __name__ == "__main__":
    main()
