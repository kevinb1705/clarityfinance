"""
KeepClarity — fortnightly goals ledger email.

Mirrors ledger.html exactly:
  - Salary movements required (fixed spending breakdown)
  - $1,000 leftover split by strict priority: India Transfer (40%) >
    Car (30%) > Investing (20%, ongoing habit, no fixed target) >
    Emergency Fund (10%). When a higher-priority goal completes, its
    share cascades proportionally to whatever's still active.
  - Realistic, pace-projected completion dates (not arbitrary deadlines)
  - Progress snapshot with a live "save a bit extra" insight
  - INVEST NOW alert whenever this fortnight's contribution crosses a
    new $1,000 milestone in the Investing pot
  - Two rotating money quotes, opening and closing

Runs every Thursday via GitHub Actions, but only actually SENDS on the
correct fortnightly week (see should_send_today), unless --force is passed.

Required environment variables (set as GitHub Actions secrets):
  EMAIL_ADDRESS       - the Gmail address sending the email
  EMAIL_APP_PASSWORD  - a Gmail App Password (not your normal password)
  RECIPIENT_EMAIL     - optional override; otherwise uses config.json
"""

import json
import math
import os
import smtplib
import sys
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

SALARY_MOVEMENTS = [
    {"label": "Rent", "amount": 450},
    {"label": "Wants", "amount": 300},
    {"label": "Needs", "amount": 300},
    {"label": "Utilities", "amount": 60},
    {"label": "Personal Wants", "amount": 60},
]

QUOTES_OPEN = [
    "Money handled with a plan stops being a source of anxiety and starts being a tool.",
    "You don't need to get this perfect every fortnight — you just need to not quit on it.",
    "A goal with a number and a date is a plan. A goal without either is a wish.",
    "The fastest way to feel in control of money is to know exactly where it's going before it leaves your account.",
]

QUOTES_CLOSE = [
    "Spending on yourself today isn't a leak in the plan — the plan exists so you can do that without guilt, not instead of it.",
    "Progress you can't see is still progress. Check back in a fortnight, not every day.",
    "The goal isn't to never spend on yourself — it's to spend on yourself and still hit the target.",
    "A dollar enjoyed responsibly is not a dollar wasted. That's the whole point of budgeting for wants in the first place.",
]

SAVINGS_FACTS = [
    "Money sitting in a high-interest savings account still grows while you wait — an emergency fund isn't 'doing nothing.'",
    "Splitting savings across several goals at once (instead of one at a time) is proven to keep motivation higher, since you see movement everywhere, not just one place.",
    "Rounding contributions to clean numbers (like $5 or $10 steps) reduces decision fatigue — you're less likely to skip a fortnight when the amount feels automatic.",
    "The gap between 'saving' and 'investing' is really about time horizon: money you'll need within a year or two is generally safer sitting in cash, not markets.",
    "A goal with a real number and a real date gets funded far more reliably than a vague 'save more' intention — specificity is most of the battle.",
    "Reviewing progress every fortnight, even briefly, is one of the strongest predictors of actually finishing a savings goal on time.",
]

LEFTOVER = 1000


# ---------------------------------------------------------------------------
# Fortnightly cadence check
# ---------------------------------------------------------------------------

def should_send_today(config, today=None):
    today = today or date.today()
    anchor = datetime.strptime(config["next_payday"], "%Y-%m-%d").date()
    days_diff = (today - anchor).days
    return days_diff % 14 == 0


def fortnight_index(payday):
    anchor = date(2026, 9, 10)
    return (payday - anchor).days // 14


# ---------------------------------------------------------------------------
# Core ledger calculation — mirrors the dashboard's JS logic exactly
# ---------------------------------------------------------------------------

WEIGHT_SETS = {
    "base":     {"india": 0.40, "car": 0.30, "invest": 0.20, "emergency": 0.10},
    "shuffled": {"india": 0.32, "car": 0.28, "invest": 0.24, "emergency": 0.16},
}


def compute_goals(config, today=None):
    next_payday = datetime.strptime(config["next_payday"], "%Y-%m-%d").date()
    income = config["income_per_fortnight"]

    idx = fortnight_index(next_payday)
    weights = WEIGHT_SETS["shuffled"] if idx % 2 != 0 else WEIGHT_SETS["base"]

    goals = []
    for g in config["goals"]:
        target = g.get("target", 0)
        if g.get("type") == "buffer":
            target = income * g["target_fortnights_of_income"]
        balance = g.get("balance", 0)
        is_ongoing = g.get("type") == "ongoing"
        remaining = None if is_ongoing else max(0, target - balance)
        goals.append({**g, "target": target, "balance": balance, "remaining": remaining, "weight": weights[g["id"]]})

    for g in goals:
        g["status"] = "active" if (g["type"] == "ongoing" or g["remaining"] > 0) else "complete"

    active = [g for g in goals if g["status"] == "active"]
    active_weight_sum = sum(g["weight"] for g in active)

    for g in goals:
        if g["status"] != "active":
            g["suggested"] = 0
            continue
        share = LEFTOVER * (g["weight"] / active_weight_sum)
        rounded = round(share / 5) * 5
        g["suggested"] = rounded if g["type"] == "ongoing" else min(g["remaining"], rounded)

    for g in goals:
        if g["type"] == "ongoing":
            g["fortnights_to_clear"] = None
            g["est_completion"] = None
        elif g["status"] == "complete":
            g["fortnights_to_clear"] = 0
            g["est_completion"] = None
        else:
            g["fortnights_to_clear"] = math.ceil(g["remaining"] / g["suggested"]) if g["suggested"] > 0 else None
            g["est_completion"] = (
                next_payday + timedelta(days=g["fortnights_to_clear"] * 14)
                if g["fortnights_to_clear"] else None
            )

    invest_goal = next((g for g in goals if g["id"] == "invest"), None)
    invest_trigger = None
    if invest_goal and invest_goal.get("suggested", 0) > 0:
        next_milestone = (invest_goal["balance"] // 1000 + 1) * 1000
        if invest_goal["balance"] + invest_goal["suggested"] >= next_milestone:
            invest_trigger = next_milestone

    return goals, next_payday, invest_trigger


# ---------------------------------------------------------------------------
# HTML email rendering
# ---------------------------------------------------------------------------

WHITE = "#ffffff"
BG = "#f5f7f6"
DGREEN = "#065f46"
DGREEN_LIGHT = "#e6f3ee"
BLUE_LIGHT = "#eaf1fe"
YELLOW = "#ca8a04"
YELLOW_LIGHT = "#fef7e0"
INK = "#0f172a"
INK_SOFT = "#475569"
INK_FAINT = "#94a3b8"
LINE = "#e2e8f0"


def fmt(n):
    return f"${round(n):,}"


def render_email_html(config, goals, next_payday, invest_trigger):
    idx = fortnight_index(next_payday)
    quote_open = QUOTES_OPEN[idx % len(QUOTES_OPEN)]
    quote_close = QUOTES_CLOSE[idx % len(QUOTES_CLOSE)]
    savings_fact = SAVINGS_FACTS[idx % len(SAVINGS_FACTS)]

    owner = config.get("github_repo_owner", "")
    repo = config.get("github_repo_name", "")
    dashboard_url = f"https://{owner}.github.io/{repo}/ledger.html" if owner and repo else "#"
    update_button = f"""
    <div style="text-align:center;margin:18px 0;">
      <a href="{dashboard_url}" style="display:inline-block;background:{DGREEN};color:#ffffff;text-decoration:none;font-weight:bold;font-size:14px;padding:12px 28px;border-radius:10px;margin:4px;">View live dashboard →</a>
    </div>
    """

    total_movements = sum(m["amount"] for m in SALARY_MOVEMENTS)
    movement_rows = "".join(
        f"<tr><td style='padding:9px 0;color:rgba(255,255,255,0.85);font-size:14px;'>{m['label']}</td>"
        f"<td align='right' style='padding:9px 0;color:#ffffff;font-family:\"Courier New\",monospace;font-size:14px;'>{fmt(m['amount'])}</td></tr>"
        for m in SALARY_MOVEMENTS
    )

    active_goals = [g for g in goals if g["status"] == "active"]
    total_suggested = sum(g.get("suggested", 0) for g in goals)
    split_rows = "".join(
        f"<tr><td style='padding:8px 0;color:{INK};font-size:14px;'>{g['name']}</td>"
        f"<td align='right' style='padding:8px 0;color:{DGREEN};font-weight:bold;font-family:\"Courier New\",monospace;font-size:14px;'>{fmt(g['suggested'])}</td></tr>"
        for g in active_goals
    ) or f"<tr><td style='color:{INK_FAINT};'>All goals complete — nothing to allocate.</td><td></td></tr>"

    non_ongoing = [g for g in goals if g["type"] != "ongoing"]
    total_target = sum(g["target"] for g in non_ongoing)
    total_balance = sum(g["balance"] for g in goals)
    overall_pct = round((sum(g["balance"] for g in non_ongoing) / total_target) * 100) if total_target else 0
    complete_count = sum(1 for g in goals if g["status"] == "complete")

    def goal_row(g):
        is_ongoing = g["type"] == "ongoing"
        pct = min(100, round((g["balance"] / g["target"]) * 100)) if (not is_ongoing and g["target"]) else 0
        if g["status"] == "complete":
            badge_text, badge_color, badge_bg = "COMPLETE", "#2563eb", BLUE_LIGHT
        elif is_ongoing:
            badge_text, badge_color, badge_bg = "ONGOING", DGREEN, DGREEN_LIGHT
        else:
            badge_text, badge_color, badge_bg = f"PRIORITY {g['priority']}", DGREEN, DGREEN_LIGHT

        if is_ongoing:
            figures = f"{fmt(g['balance'])} saved so far &middot; {fmt(g['suggested'])}/fortnight"
        elif g["status"] == "complete":
            figures = f"{fmt(g['balance'])} / {fmt(g['target'])} &middot; complete"
        else:
            date_str = g["est_completion"].strftime("%d %b %Y") if g["est_completion"] else "—"
            figures = f"{fmt(g['balance'])} / {fmt(g['target'])} &middot; {pct}% &middot; est. complete {date_str}"

        bar_html = "" if is_ongoing else f"""
            <tr><td colspan="2" style="padding-top:8px;">
              <div style="background:#eef2f0;border-radius:20px;height:7px;width:100%;">
                <div style="background:{DGREEN};height:100%;width:{pct}%;border-radius:20px;"></div>
              </div>
            </td></tr>"""

        return f"""
        <tr><td style="padding:12px 0;border-bottom:1px solid {LINE};">
          <table width="100%">
            <tr>
              <td style="font-weight:bold;font-size:14.5px;color:{INK};">{g['name']}</td>
              <td align="right"><span style="font-size:10px;letter-spacing:1px;font-weight:bold;color:{badge_color};background:{badge_bg};padding:3px 9px;border-radius:20px;">{badge_text}</span></td>
            </tr>
            {bar_html}
            <tr><td colspan="2" style="padding-top:6px;font-family:'Courier New',monospace;font-size:12px;color:{INK_FAINT};">{figures}</td></tr>
          </table>
        </td></tr>
        """

    goal_rows_html = "".join(goal_row(g) for g in goals)

    def tracker_row(g):
        is_ongoing = g["type"] == "ongoing"
        target_str = "Ongoing" if is_ongoing else fmt(g["target"])
        pct = "—" if is_ongoing else f"{min(100, round((g['balance']/g['target'])*100)) if g['target'] else 0}%"
        return f"""
        <tr>
          <td style="padding:10px 8px;border-bottom:1px solid {LINE};font-size:13.5px;color:{INK};font-weight:bold;">{g['name']}</td>
          <td style="padding:10px 8px;border-bottom:1px solid {LINE};font-family:'Courier New',monospace;font-size:13.5px;color:{DGREEN};text-align:right;">{fmt(g['balance'])}</td>
          <td style="padding:10px 8px;border-bottom:1px solid {LINE};font-family:'Courier New',monospace;font-size:13.5px;color:{INK_FAINT};text-align:right;">{target_str}</td>
          <td style="padding:10px 8px;border-bottom:1px solid {LINE};font-family:'Courier New',monospace;font-size:13.5px;color:{INK_FAINT};text-align:right;">{pct}</td>
        </tr>
        """

    tracker_rows_html = "".join(tracker_row(g) for g in goals)

    active_with_pace = [g for g in active_goals if g.get("suggested", 0) > 0 and g["type"] != "ongoing" and g["fortnights_to_clear"]]
    fun_fact_html = "Every active goal is on a clear pace right now — nothing urgent to adjust this fortnight."
    if active_with_pace:
        slowest = max(active_with_pace, key=lambda g: g["fortnights_to_clear"])
        extra = 15
        new_suggested = slowest["suggested"] + extra
        new_fortnights = math.ceil(slowest["remaining"] / new_suggested)
        saved = slowest["fortnights_to_clear"] - new_fortnights
        if saved > 0:
            fun_fact_html = (
                f"If you add just {fmt(extra)} more to <strong>{slowest['name']}</strong> this fortnight, "
                f"you'd reach it <strong>{saved} fortnight{'s' if saved != 1 else ''} sooner</strong> — "
                f"that's the goal currently taking the longest."
            )
        else:
            fun_fact_html = f"<strong>{slowest['name']}</strong> is your slowest-moving goal right now — even a small top-up here has an outsized effect on when it finishes."

    invest_goal_for_milestones = next((g for g in goals if g["id"] == "invest"), None)
    milestone_html = ""
    if invest_goal_for_milestones:
        bal = invest_goal_for_milestones["balance"]
        hit_count = bal // 1000
        chip_count = max(hit_count + 2, 3)
        chips = []
        for i in range(1, chip_count + 1):
            is_hit = i <= hit_count
            is_next = i == hit_count + 1
            if is_hit:
                style = f"background:{DGREEN_LIGHT};border:1.5px solid {DGREEN};color:{DGREEN};"
                label = f"✓ {fmt(i*1000)}"
            elif is_next:
                style = f"background:{YELLOW_LIGHT};border:1.5px solid {YELLOW};color:{YELLOW};"
                label = f"{fmt(i*1000)} next"
            else:
                style = f"background:#ffffff;border:1.5px solid {LINE};color:{INK_FAINT};"
                label = fmt(i*1000)
            chips.append(f"<span style='display:inline-block;font-family:\"Courier New\",monospace;font-size:12px;font-weight:bold;padding:5px 11px;border-radius:20px;margin:3px;{style}'>{label}</span>")
        milestone_html = f"""
        <div style="margin-top:16px;padding-top:14px;border-top:1px solid {LINE};">
          <div style="font-size:12.5px;font-weight:bold;color:{INK_SOFT};margin-bottom:8px;">Investing milestones — {fmt(bal)} saved so far</div>
          <div>{''.join(chips)}</div>
        </div>
        """

    invest_banner_html = ""
    if invest_trigger:
        invest_banner_html = f"""
        <div style="font-family:'Courier New',monospace;font-size:11px;letter-spacing:2px;color:{YELLOW};text-transform:uppercase;margin:26px 0 10px;">Alert</div>
        <div style="background:{YELLOW_LIGHT};border:2px solid {YELLOW};border-radius:16px;padding:18px 20px;">
          <div style="font-weight:bold;color:{YELLOW};font-size:16px;margin-bottom:4px;">🚀 INVEST NOW — you're about to hit {fmt(invest_trigger)}</div>
          <div style="font-size:13.5px;color:{INK_SOFT};">This fortnight's Investing contribution takes your pot past {fmt(invest_trigger)}. Time to actually place that investment, not just let it sit as cash.</div>
        </div>
        """

    return f"""
    <html><body style="margin:0;background:{BG};font-family:Arial,sans-serif;">
    <div style="max-width:640px;margin:0 auto;padding:28px 18px;">

      <div style="background:{WHITE};border:1px solid {LINE};border-radius:16px;padding:20px 24px;margin-bottom:18px;">
        <div style="font-size:11px;color:{INK_FAINT};font-weight:bold;text-transform:uppercase;letter-spacing:1px;">KeepClarity &middot; Fortnightly ledger</div>
        <div style="font-weight:800;font-size:17px;color:{DGREEN};">Where your pay should go — {next_payday.strftime('%d %b %Y')}</div>
      </div>

      {update_button}

      <div style="background:{WHITE};border:1px solid {LINE};border-radius:16px;padding:22px 26px;text-align:center;margin-bottom:6px;">
        <div style="color:{YELLOW};font-size:26px;line-height:1;">&ldquo;</div>
        <div style="font-style:italic;font-weight:600;color:{DGREEN};font-size:15px;">{quote_open}</div>
      </div>

      <div style="font-family:'Courier New',monospace;font-size:11px;letter-spacing:2px;color:{INK_FAINT};text-transform:uppercase;margin:26px 0 10px;">Salary movements required</div>
      <div style="background:{DGREEN};border-radius:16px;padding:20px 24px;">
        <table width="100%">{movement_rows}
          <tr><td style="padding-top:14px;border-top:2px solid rgba(255,255,255,0.4);color:#ffffff;font-weight:bold;font-size:16px;">Total salary movements</td>
              <td align="right" style="padding-top:14px;border-top:2px solid rgba(255,255,255,0.4);color:#ffffff;font-weight:bold;font-family:'Courier New',monospace;font-size:16px;">{fmt(total_movements)}</td></tr>
        </table>
      </div>

      {invest_banner_html}

      <div style="font-family:'Courier New',monospace;font-size:11px;letter-spacing:2px;color:{INK_FAINT};text-transform:uppercase;margin:26px 0 10px;">This fortnight's goal split (by priority)</div>
      <div style="background:{WHITE};border:1px solid {LINE};border-radius:16px;padding:20px 24px;">
        <table width="100%">{split_rows}
          <tr><td style="padding-top:14px;border-top:2px solid {DGREEN};color:{DGREEN};font-weight:bold;font-size:16px;">Total to set aside</td>
              <td align="right" style="padding-top:14px;border-top:2px solid {DGREEN};color:{DGREEN};font-weight:bold;font-family:'Courier New',monospace;font-size:16px;">{fmt(total_suggested)}</td></tr>
        </table>
      </div>

      <div style="font-family:'Courier New',monospace;font-size:11px;letter-spacing:2px;color:{INK_FAINT};text-transform:uppercase;margin:26px 0 10px;">Manual balance tracker</div>
      <div style="background:{WHITE};border:1px solid {LINE};border-radius:16px;padding:20px 24px;">
        <div style="font-size:12.5px;color:{INK_SOFT};margin-bottom:12px;">This is what was in your goals as of this email. Update your balances on the live dashboard, and this table refreshes on the next email.</div>
        <table width="100%" style="border-collapse:collapse;">
          <tr>
            <th style="text-align:left;padding:8px;font-size:10.5px;color:{INK_FAINT};text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid {LINE};">Goal</th>
            <th style="text-align:right;padding:8px;font-size:10.5px;color:{INK_FAINT};text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid {LINE};">Balance</th>
            <th style="text-align:right;padding:8px;font-size:10.5px;color:{INK_FAINT};text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid {LINE};">Target</th>
            <th style="text-align:right;padding:8px;font-size:10.5px;color:{INK_FAINT};text-transform:uppercase;letter-spacing:0.5px;border-bottom:2px solid {LINE};">%</th>
          </tr>
          {tracker_rows_html}
          <tr>
            <td style="padding:10px 8px;font-weight:bold;font-size:13.5px;color:{DGREEN};">Total</td>
            <td style="padding:10px 8px;font-family:'Courier New',monospace;font-size:13.5px;color:{DGREEN};text-align:right;font-weight:bold;">{fmt(total_balance)}</td>
            <td colspan="2"></td>
          </tr>
        </table>
      </div>

      <div style="font-family:'Courier New',monospace;font-size:11px;letter-spacing:2px;color:{INK_FAINT};text-transform:uppercase;margin:26px 0 10px;">Progress snapshot</div>
      <div style="background:{WHITE};border:1px solid {LINE};border-radius:16px;padding:20px 24px;">
        <table width="100%" style="margin-bottom:14px;">
          <tr>
            <td style="background:{BG};border:1px solid {LINE};border-radius:12px;padding:12px 14px;width:33%;">
              <div style="font-size:10px;color:{INK_FAINT};font-weight:bold;text-transform:uppercase;">Complete</div>
              <div style="font-family:'Courier New',monospace;font-size:18px;font-weight:bold;color:{DGREEN};">{complete_count} / {len(goals)}</div>
            </td>
            <td style="background:{BG};border:1px solid {LINE};border-radius:12px;padding:12px 14px;width:33%;">
              <div style="font-size:10px;color:{INK_FAINT};font-weight:bold;text-transform:uppercase;">Overall</div>
              <div style="font-family:'Courier New',monospace;font-size:18px;font-weight:bold;color:{DGREEN};">{overall_pct}%</div>
            </td>
            <td style="background:{BG};border:1px solid {LINE};border-radius:12px;padding:12px 14px;width:33%;">
              <div style="font-size:10px;color:{INK_FAINT};font-weight:bold;text-transform:uppercase;">Saved</div>
              <div style="font-family:'Courier New',monospace;font-size:18px;font-weight:bold;color:{DGREEN};">{fmt(total_balance)}</div>
            </td>
          </tr>
        </table>
        <table width="100%">{goal_rows_html}</table>
        {milestone_html}
        <div style="background:{BLUE_LIGHT};border-radius:12px;padding:14px 16px;margin-top:14px;font-size:13.5px;color:{INK};">💡 {fun_fact_html}</div>
        <div style="background:{DGREEN_LIGHT};border-radius:12px;padding:14px 16px;margin-top:10px;font-size:13.5px;color:{INK};">📊 {savings_fact}</div>
      </div>

      {update_button}

      <div style="background:{WHITE};border:1px solid {LINE};border-radius:16px;padding:22px 26px;text-align:center;margin-top:20px;">
        <div style="color:{YELLOW};font-size:26px;line-height:1;">&ldquo;</div>
        <div style="font-style:italic;font-weight:600;color:{DGREEN};font-size:15px;">{quote_close}</div>
      </div>

      <div style="text-align:center;font-family:'Courier New',monospace;font-size:10px;color:{INK_FAINT};letter-spacing:1px;margin-top:26px;text-transform:uppercase;">
        keepclarity &middot; tap "View live dashboard" above to update your balances
      </div>
    </div>
    </body></html>
    """


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------

def send_email(html_body, config):
    sender = os.environ["EMAIL_ADDRESS"]
    app_password = os.environ["EMAIL_APP_PASSWORD"]
    recipient = os.environ.get("RECIPIENT_EMAIL") or config["recipient_email"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Where your pay should go — {datetime.now().strftime('%d %b %Y, %I:%M%p')}"
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(sender, app_password)
        server.sendmail(sender, recipient, msg.as_string())

    print(f"Sent to {recipient}")


def main():
    with open(CONFIG_PATH) as f:
        config = json.load(f)

    force = "--force" in sys.argv
    if not force and not should_send_today(config):
        print("Not a payday fortnight — skipping send. Use --force to override.")
        return

    goals, next_payday, invest_trigger = compute_goals(config)
    html = render_email_html(config, goals, next_payday, invest_trigger)
    send_email(html, config)


if __name__ == "__main__":
    main()
