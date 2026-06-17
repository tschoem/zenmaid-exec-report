#!/usr/bin/env python3
"""
Build a board-ready sales performance dashboard from ZenMaid appointment exports.

Usage:
  python3 build_board_dashboard.py \
    --input "input_data/your_export.csv" \
    --output "board_dashboard.html"
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a branded board dashboard from ZenMaid export."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to ZenMaid appointment CSV export.",
    )
    parser.add_argument(
        "--output",
        default="board_dashboard.html",
        help="Output HTML path (default: board_dashboard.html).",
    )
    parser.add_argument(
        "--date-overrides",
        default="",
        help="Optional JSON file mapping Appointment ID -> corrected date (YYYY-MM-DD).",
    )
    return parser.parse_args()


def to_float(raw: str) -> float:
    if raw is None:
        return 0.0
    txt = str(raw).strip().replace(",", "")
    if txt == "":
        return 0.0
    try:
        return float(txt)
    except ValueError:
        return 0.0


def parse_date(date_text: str) -> dt.date | None:
    if not date_text:
        return None
    try:
        return dt.datetime.strptime(date_text.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def month_key(d: dt.date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def month_label(month: str) -> str:
    y, m = month.split("-")
    date_obj = dt.date(int(y), int(m), 1)
    return date_obj.strftime("%b %Y")


def customer_key(row: Dict[str, str]) -> str:
    cid = (row.get("Customer ID") or "").strip()
    if cid:
        return f"id:{cid}"
    email = (row.get("Customer Emails") or row.get("Customer Email 1") or "").strip().lower()
    if email:
        return f"email:{email}"
    name = (row.get("Customer Full Name") or "").strip().lower()
    if name:
        return f"name:{name}"
    return ""


def customer_display_name(row: Dict[str, str]) -> str:
    full_name = (row.get("Customer Full Name") or "").strip()
    if full_name:
        return full_name
    company = (row.get("Customer Company Name") or "").strip()
    if company:
        return company
    email = (row.get("Customer Emails") or row.get("Customer Email 1") or "").strip()
    if email:
        return email
    cid = (row.get("Customer ID") or "").strip()
    if cid:
        return f"Customer {cid}"
    return "Unknown Customer"


def appointment_revenue(row: Dict[str, str]) -> float:
    # Net/base revenue only (exclude tax).
    return to_float(row.get("Price", "0"))


def clean_category(row: Dict[str, str]) -> str:
    raw = (row.get("Service Type") or "").strip()
    if not raw:
        raw = "Unspecified"
    return raw


def load_date_overrides(path_text: str) -> Dict[str, dt.date]:
    if not path_text:
        return {}
    path = Path(path_text).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Date override file not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Date override file must be a JSON object of {Appointment ID: date}.")

    parsed: Dict[str, dt.date] = {}
    for appt_id, date_text in raw.items():
        try:
            d = dt.datetime.strptime(str(date_text).strip(), "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError(
                f"Invalid override date for Appointment ID {appt_id}: {date_text}. "
                "Expected YYYY-MM-DD."
            ) from exc
        parsed[str(appt_id)] = d
    return parsed


def read_appointments(csv_path: Path, date_overrides: Dict[str, dt.date] | None = None) -> List[Dict]:
    appointments: List[Dict] = []
    overrides = date_overrides or {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        # First line is export description, second line has headers.
        _ = handle.readline()
        reader = csv.DictReader(handle)
        for row in reader:
            appt_id = (row.get("Appointment ID") or "").strip()
            if appt_id and appt_id in overrides:
                date_obj = overrides[appt_id]
            else:
                date_obj = parse_date((row.get("Appointment Date") or "").strip())
            if date_obj is None:
                continue

            status = (row.get("Appointment Status") or "").strip().lower()
            if status in {"cancelled", "holiday calendar", "stand-by", "stand by"}:
                continue

            cust = customer_key(row)
            if not cust:
                continue

            revenue = appointment_revenue(row)
            if revenue < 0:
                continue
            paid_raw = (row.get("Paid") or "").strip().lower()
            is_paid = paid_raw in {"true", "1", "yes"}

            appointments.append(
                {
                    "date": date_obj,
                    "month": month_key(date_obj),
                    "customer": cust,
                    "customer_display": customer_display_name(row),
                    "category": clean_category(row),
                    "revenue": round(revenue, 2),
                    "paid": is_paid,
                }
            )
    return appointments


def build_metrics(appointments: List[Dict]) -> Dict:
    if not appointments:
        return {
            "months": [],
            "kpis": {},
            "monthlyRevenue": [],
            "categorySeries": [],
            "customerSeries": [],
            "newBusiness": [],
            "recurringAndChurn": [],
            "unpaidImpact": [],
            "appointmentEvents": [],
            "topCategories": [],
            "topCustomers": [],
            "executiveSummary": {"headline": "", "highlights": [], "watchouts": []},
            "definitions": {
                "new_business": "Customer's first completed month in the dataset.",
                "recurring_business": "Revenue from customers whose first completed month is before the month.",
                "churn": "Customers active in prior month but inactive in current month.",
            },
        }

    appointments.sort(key=lambda x: x["date"])
    months_sorted = sorted({a["month"] for a in appointments})

    rev_by_month: Dict[str, float] = defaultdict(float)
    active_customers_by_month: Dict[str, Set[str]] = defaultdict(set)
    rev_by_category_month: Dict[Tuple[str, str], float] = defaultdict(float)
    customer_total_revenue: Dict[str, float] = defaultdict(float)
    customer_display_by_key: Dict[str, str] = {}
    first_month_by_customer: Dict[str, str] = {}
    unpaid_count_by_month: Dict[str, int] = defaultdict(int)
    unpaid_revenue_by_month: Dict[str, float] = defaultdict(float)

    for appt in appointments:
        month = appt["month"]
        customer = appt["customer"]
        revenue = appt["revenue"]
        category = appt["category"]

        rev_by_month[month] += revenue
        active_customers_by_month[month].add(customer)
        rev_by_category_month[(category, month)] += revenue
        customer_total_revenue[customer] += revenue
        if customer not in customer_display_by_key or not customer_display_by_key[customer]:
            customer_display_by_key[customer] = appt.get("customer_display", customer)
        if not appt.get("paid", True):
            unpaid_count_by_month[month] += 1
            unpaid_revenue_by_month[month] += revenue

        if customer not in first_month_by_customer:
            first_month_by_customer[customer] = month

    new_customers_by_month: Dict[str, int] = defaultdict(int)
    for _, first_month in first_month_by_customer.items():
        new_customers_by_month[first_month] += 1

    new_business_revenue_month: Dict[str, float] = defaultdict(float)
    recurring_revenue_month: Dict[str, float] = defaultdict(float)
    recurring_customers_month: Dict[str, Set[str]] = defaultdict(set)

    for appt in appointments:
        month = appt["month"]
        customer = appt["customer"]
        revenue = appt["revenue"]
        if first_month_by_customer[customer] == month:
            new_business_revenue_month[month] += revenue
        else:
            recurring_revenue_month[month] += revenue
            recurring_customers_month[month].add(customer)

    monthly_revenue = []
    new_business = []
    recurring_and_churn = []
    unpaid_impact = []

    total_revenue = 0.0
    total_appointments = len(appointments)
    churn_events = 0
    churn_base = 0

    for idx, month in enumerate(months_sorted):
        month_rev = round(rev_by_month[month], 2)
        total_revenue += month_rev
        active_customers = active_customers_by_month[month]

        monthly_revenue.append(
            {
                "month": month,
                "label": month_label(month),
                "revenue": month_rev,
                "active_customers": len(active_customers),
            }
        )

        new_business.append(
            {
                "month": month,
                "label": month_label(month),
                "new_customers": new_customers_by_month[month],
                "new_business_revenue": round(new_business_revenue_month[month], 2),
            }
        )

        prev_customers = active_customers_by_month[months_sorted[idx - 1]] if idx > 0 else set()
        retained = len(prev_customers.intersection(active_customers)) if idx > 0 else 0
        churned = len(prev_customers - active_customers) if idx > 0 else 0
        churn_rate = (churned / len(prev_customers)) if idx > 0 and prev_customers else 0.0

        churn_events += churned
        churn_base += len(prev_customers) if idx > 0 else 0

        recurring_and_churn.append(
            {
                "month": month,
                "label": month_label(month),
                "recurring_revenue": round(recurring_revenue_month[month], 2),
                "recurring_customers": len(recurring_customers_month[month]),
                "retained_customers": retained,
                "churned_customers": churned,
                "churn_rate": round(churn_rate, 4),
            }
        )
        unpaid_impact.append(
            {
                "month": month,
                "label": month_label(month),
                "unpaid_count": unpaid_count_by_month[month],
                "unpaid_revenue": round(unpaid_revenue_by_month[month], 2),
            }
        )

    categories = sorted({a["category"] for a in appointments})
    category_series = []
    top_categories_rank: List[Tuple[str, float]] = []
    for category in categories:
        series = [round(rev_by_category_month[(category, m)], 2) for m in months_sorted]
        total_cat = round(sum(series), 2)
        top_categories_rank.append((category, total_cat))
        category_series.append(
            {
                "category": category,
                "monthly": series,
                "total_revenue": total_cat,
            }
        )

    top_categories = [
        {"category": cat, "revenue": round(rev, 2)}
        for cat, rev in sorted(top_categories_rank, key=lambda x: x[1], reverse=True)[:8]
    ]

    top_customers = [
        {"customer": customer_display_by_key.get(cust, cust), "revenue": round(rev, 2)}
        for cust, rev in sorted(customer_total_revenue.items(), key=lambda x: x[1], reverse=True)[:12]
    ]
    customer_series = []
    for customer, _ in sorted(customer_total_revenue.items(), key=lambda x: x[1], reverse=True):
        monthly = []
        for m in months_sorted:
            monthly.append(
                round(
                    sum(
                        a["revenue"]
                        for a in appointments
                        if a["customer"] == customer and a["month"] == m
                    ),
                    2,
                )
            )
        customer_series.append(
            {
                "customer": customer,
                "customer_display": customer_display_by_key.get(customer, customer),
                "monthly": monthly,
                "total_revenue": round(sum(monthly), 2),
            }
        )

    first_month = months_sorted[0]
    last_month = months_sorted[-1]
    first_rev = rev_by_month[first_month]
    last_rev = rev_by_month[last_month]
    mom_growth_last = 0.0
    if len(months_sorted) > 1:
        prev = rev_by_month[months_sorted[-2]]
        if prev != 0:
            mom_growth_last = (last_rev - prev) / prev

    kpis = {
        "period_start": first_month,
        "period_end": last_month,
        "total_revenue": round(total_revenue, 2),
        "avg_monthly_revenue": round(total_revenue / len(months_sorted), 2),
        "total_appointments": total_appointments,
        "avg_appointment_value": round(total_revenue / total_appointments, 2) if total_appointments else 0.0,
        "unique_customers": len(first_month_by_customer),
        "last_month_revenue": round(last_rev, 2),
        "last_month_mom_growth": round(mom_growth_last, 4),
        "net_growth_vs_first_month": round(((last_rev - first_rev) / first_rev), 4) if first_rev else 0.0,
        "dataset_churn_rate": round((churn_events / churn_base), 4) if churn_base else 0.0,
    }

    best_month = max(monthly_revenue, key=lambda x: x["revenue"])
    worst_month = min(monthly_revenue, key=lambda x: x["revenue"])
    last_rec = recurring_and_churn[-1] if recurring_and_churn else {}
    last_new = new_business[-1] if new_business else {}

    executive_summary = {
        "headline": f"Revenue is {kpis['net_growth_vs_first_month']:.1%} vs the first month in this period.",
        "highlights": [
            f"Total revenue for the period is EUR {kpis['total_revenue']:,.0f} across {kpis['total_appointments']} completed appointments.",
            f"Best month by revenue: {best_month['label']} at EUR {best_month['revenue']:,.0f}.",
            f"Top revenue category: {top_categories[0]['category']} at EUR {top_categories[0]['revenue']:,.0f}."
            if top_categories
            else "No category data available.",
            f"Latest month new business: {last_new.get('new_customers', 0)} customers and EUR {last_new.get('new_business_revenue', 0):,.0f}.",
        ],
        "watchouts": [
            f"Latest month revenue: EUR {kpis['last_month_revenue']:,.0f} ({kpis['last_month_mom_growth']:.1%} vs prior month).",
            f"Latest month churn: {last_rec.get('churned_customers', 0)} customers ({last_rec.get('churn_rate', 0):.1%}).",
            f"Lowest month by revenue: {worst_month['label']} at EUR {worst_month['revenue']:,.0f}.",
        ],
    }

    return {
        "months": months_sorted,
        "monthLabels": [month_label(m) for m in months_sorted],
        "kpis": kpis,
        "monthlyRevenue": monthly_revenue,
        "categorySeries": category_series,
        "customerSeries": customer_series,
        "newBusiness": new_business,
        "recurringAndChurn": recurring_and_churn,
        "unpaidImpact": unpaid_impact,
        "appointmentEvents": [
            {"date": a["date"].isoformat(), "customer": a["customer"]}
            for a in appointments
        ],
        "topCategories": top_categories,
        "topCustomers": top_customers,
        "executiveSummary": executive_summary,
        "definitions": {
            "new_business": "Customer's first completed month in the dataset; revenue includes all their completed appointments in that first month.",
            "recurring_business": "Revenue from customers whose first completed month is earlier than the current month.",
            "churn": "Customers active in prior month but inactive in current month (month-to-month).",
        },
    }


def render_html(data_with_unpaid: Dict, data_paid_only: Dict) -> str:
    payload_with_unpaid = json.dumps(data_with_unpaid)
    payload_paid_only = json.dumps(data_paid_only)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>C&N Clean & Neat | Board Sales Dashboard</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    :root {{
      --bg: #f4f8f3;
      --card: #ffffff;
      --ink: #2a2a2a;
      --sub: #5d5d5d;
      --brand: #56b665;
      --brand-2: #75d69c;
      --accent: #6dab3c;
      --ok: #56b665;
      --danger: #c94040;
      --line: #d9e8d8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Avenir Next", "Segoe UI", Arial, sans-serif;
      color: var(--ink);
      background: radial-gradient(circle at top right, #e9f7e9, var(--bg) 60%);
    }}
    .wrap {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}
    .hero {{
      background: linear-gradient(135deg, var(--brand), var(--brand-2));
      color: #fff;
      border-radius: 16px;
      padding: 24px;
      box-shadow: 0 12px 28px rgba(16, 62, 52, 0.2);
    }}
    .hero-head {{
      display: flex;
      gap: 14px;
      align-items: center;
      flex-wrap: wrap;
    }}
    .brand-logo {{
      width: 58px;
      height: 58px;
      border-radius: 8px;
      border: 1px solid rgba(255, 255, 255, 0.35);
      background: rgba(255, 255, 255, 0.12);
      object-fit: contain;
      padding: 6px;
    }}
    .hero h1 {{ margin: 0; font-size: 32px; }}
    .hero p {{ margin: 8px 0 0; opacity: 0.95; }}
    .kpi-grid {{
      margin-top: 18px;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
    }}
    .kpi {{
      background: rgba(255, 255, 255, 0.14);
      border: 1px solid rgba(255, 255, 255, 0.25);
      border-radius: 12px;
      padding: 12px;
    }}
    .kpi .label {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; opacity: 0.9; }}
    .kpi .value {{ margin-top: 6px; font-size: 24px; font-weight: 700; }}
    .section {{
      margin-top: 18px;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 16px;
      box-shadow: 0 8px 22px rgba(29, 66, 57, 0.06);
    }}
    .section h2 {{
      margin: 0 0 10px;
      font-size: 18px;
      color: var(--ink);
    }}
    .section p.sub {{ margin: 0 0 12px; color: var(--sub); }}
    .chart {{ min-height: 380px; }}
    .filter-bar {{
      margin-top: 16px;
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: end;
    }}
    .hero .filter-bar {{ margin-top: 14px; }}
    .hero .filter-item label {{ color: rgba(255, 255, 255, 0.88); }}
    .hero .filter-item select {{
      background: rgba(255, 255, 255, 0.14);
      color: #fff;
      border: 1px solid rgba(255, 255, 255, 0.35);
    }}
    .hero .filter-item select option {{
      color: #2a2a2a;
      background: #ffffff;
    }}
    .preset-row {{
      margin-top: 8px;
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .preset-btn {{
      border: 1px solid #cde7cf;
      background: #eef8ef;
      color: #2f6d3d;
      border-radius: 10px;
      padding: 7px 10px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 600;
    }}
    .preset-btn:hover {{
      background: #e4f3e6;
    }}
    .hero .preset-btn {{
      background: rgba(255, 255, 255, 0.16);
      color: #fff;
      border: 1px solid rgba(255, 255, 255, 0.35);
    }}
    .hero .preset-btn:hover {{
      background: rgba(255, 255, 255, 0.24);
    }}
    .hero-compare {{
      margin-top: 8px;
      font-size: 13px;
      color: rgba(255, 255, 255, 0.92);
    }}
    .filter-item label {{
      display: block;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--sub);
      margin-bottom: 4px;
    }}
    .filter-item select {{
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 8px 10px;
      min-width: 150px;
      font-size: 14px;
      color: var(--ink);
      background: #fff;
    }}
    .exec-grid {{
      display: grid;
      grid-template-columns: 1.1fr 1fr;
      gap: 16px;
    }}
    .exec-card {{
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
    }}
    .exec-headline {{
      margin: 0 0 8px;
      font-size: 20px;
      color: var(--brand);
      font-weight: 800;
    }}
    .exec-list {{
      margin: 0;
      padding-left: 18px;
      line-height: 1.5;
      color: var(--ink);
    }}
    .exec-list li {{ margin: 8px 0; }}
    .pill-row {{
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(2, minmax(150px, 1fr));
      margin-top: 12px;
    }}
    .pill {{
      background: linear-gradient(180deg, #f2fbf2, #e8f5e8);
      border: 1px solid #cde7cf;
      border-radius: 12px;
      padding: 10px;
    }}
    .pill .label {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.07em; color: #4b7c52; }}
    .pill .value {{ margin-top: 5px; font-size: 22px; font-weight: 800; color: #2f6d3d; }}
    .mini-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(200px, 1fr));
      gap: 10px;
      margin: 8px 0 12px;
    }}
    .mini-card {{
      background: #f7fbf7;
      border: 1px solid #d8ead8;
      border-radius: 10px;
      padding: 10px;
    }}
    .mini-card .label {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; color: #4f6f66; }}
    .mini-card .value {{ margin-top: 4px; font-size: 22px; font-weight: 800; color: #2a2a2a; }}
    .pie-compare-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }}
    .pie-panel-title {{
      margin: 0 0 6px;
      font-size: 13px;
      color: var(--sub);
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }}
    .hidden {{ display: none !important; }}
    .two-col {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      padding: 8px;
      border-bottom: 1px solid var(--line);
      text-align: left;
    }}
    th {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--sub); }}
    .note {{
      font-size: 12px;
      color: var(--sub);
      margin-top: 12px;
      line-height: 1.45;
    }}
    @media (max-width: 980px) {{
      .two-col {{ grid-template-columns: 1fr; }}
      .exec-grid {{ grid-template-columns: 1fr; }}
      .pie-compare-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="hero-head">
        <img class="brand-logo" src="https://cleanandneat.ie/wp-content/uploads/2024/07/White-suprer-bold-50x50-transparent-logo.png" alt="C&N logo" />
        <div>
          <h1>C&N Clean and Neat House and Garden Doctors</h1>
          <p>Board Sales Performance Dashboard</p>
        </div>
      </div>
      <div class="filter-bar">
        <div class="filter-item">
          <label for="startMonth">Start month</label>
          <select id="startMonth"></select>
        </div>
        <div class="filter-item">
          <label for="endMonth">End month</label>
          <select id="endMonth"></select>
        </div>
        <div class="filter-item">
          <label for="comparisonMode">Comparison</label>
          <select id="comparisonMode">
            <option value="none">No comparison</option>
            <option value="previous_period">Vs previous period</option>
            <option value="previous_year">Vs same months last year</option>
            <option value="baseline_first">Vs first month in dataset</option>
          </select>
        </div>
        <div class="filter-item">
          <label for="includeUnpaid">Include unpaid</label>
          <select id="includeUnpaid">
            <option value="no">No (paid only)</option>
            <option value="yes">Yes (paid + unpaid)</option>
          </select>
        </div>
        <div class="filter-item">
          <label for="churnWeeks">Churn window (weeks)</label>
          <select id="churnWeeks">
            <option value="4">4</option>
            <option value="5">5</option>
            <option value="6">6</option>
            <option value="7">7</option>
            <option value="8">8</option>
          </select>
        </div>
      </div>
      <div class="preset-row">
        <button class="preset-btn" id="presetAll" type="button">All time</button>
        <button class="preset-btn" id="presetYtd" type="button">YTD</button>
        <button class="preset-btn" id="presetLast3" type="button">Last 3 months</button>
        <button class="preset-btn" id="presetLast6" type="button">Last 6 months</button>
      </div>
      <p id="comparisonSummary" class="hero-compare"></p>
      <div id="kpis" class="kpi-grid"></div>
    </section>

    <section class="section">
      <h2>Executive Summary (Board Slide)</h2>
      <p class="sub">One-slide view for presenting progress, momentum, and key watchouts.</p>
      <div class="exec-grid">
        <div class="exec-card">
          <p id="execHeadline" class="exec-headline"></p>
          <ul id="execHighlights" class="exec-list"></ul>
        </div>
        <div class="exec-card">
          <h3 style="margin:0 0 8px;color:#2f6d3d;">Watchouts</h3>
          <ul id="execWatchouts" class="exec-list"></ul>
          <div class="pill-row">
            <div class="pill"><div class="label">Last Month Revenue</div><div id="pillRevenue" class="value">-</div></div>
            <div class="pill"><div class="label">Last Month New Customers</div><div id="pillNewCust" class="value">-</div></div>
            <div class="pill"><div class="label">Churn % (last month in range)</div><div id="pillChurn" class="value">-</div></div>
            <div class="pill"><div class="label">Average Ticket</div><div id="pillAov" class="value">-</div></div>
          </div>
        </div>
      </div>
    </section>

    <section class="section">
      <h2>Revenue Per Category (Appointment Type) Per Month</h2>
      <p class="sub">Monthly gross revenue by service category from completed appointments.</p>
      <div id="categoryRevenueChart" class="chart"></div>
    </section>

    <section class="section">
      <h2>Unpaid Appointments</h2>
      <p class="sub">Volume and revenue exposure from appointments marked unpaid in the selected period.</p>
      <div class="mini-grid">
        <div class="mini-card">
          <div class="label">Unpaid Appointment Volume</div>
          <div id="unpaidVolumeValue" class="value">0</div>
        </div>
        <div class="mini-card">
          <div class="label">Impacted Revenue</div>
          <div id="unpaidRevenueValue" class="value">€0</div>
        </div>
      </div>
      <div id="unpaidImpactChart" class="chart"></div>
    </section>

    <section class="two-col">
      <section class="section">
        <h2>New Business Per Month</h2>
        <p class="sub">New customers and first-month revenue contribution.</p>
        <div id="newBusinessChart" class="chart"></div>
      </section>
      <section class="section">
        <h2>Recurring Business & Churn</h2>
        <p class="sub">Recurring revenue, monthly churned customers, and churn rate.</p>
        <div id="recurringChurnChart" class="chart"></div>
      </section>
    </section>

    <section class="two-col">
      <section class="section">
        <h2>Top Categories</h2>
        <p class="sub">Revenue share by category for selected period.</p>
        <div class="pie-compare-grid">
          <div id="topCategoriesCurrentPanel">
            <p class="pie-panel-title">Selected period</p>
            <div id="topCategoriesPieCurrent" class="chart"></div>
          </div>
          <div id="topCategoriesComparePanel">
            <p class="pie-panel-title" id="topCategoriesPieCompareTitle">Comparison period</p>
            <div id="topCategoriesPieCompare" class="chart"></div>
          </div>
        </div>
      </section>
      <section class="section">
        <h2>Top Customers</h2>
        <table>
          <thead><tr><th>Customer Key</th><th>Revenue</th></tr></thead>
          <tbody id="topCustomersBody"></tbody>
        </table>
      </section>
    </section>

    <section class="section">
      <h2>Monthly Executive Trend</h2>
      <p class="sub">Total revenue and active customer trend.</p>
      <div id="monthlyTrendChart" class="chart"></div>
      <div class="note" id="definitions"></div>
    </section>
  </div>

  <script>
    const datasets = {{
      with_unpaid: {payload_with_unpaid},
      paid_only: {payload_paid_only}
    }};
    let data = datasets.paid_only;
    const euro = new Intl.NumberFormat('en-IE', {{ style: 'currency', currency: 'EUR', maximumFractionDigits: 0 }});
    const pct = new Intl.NumberFormat('en-IE', {{ style: 'percent', maximumFractionDigits: 1 }});
    let months = data.months || [];
    let monthLabels = data.monthLabels || [];

    function sum(arr) {{
      return arr.reduce((a, b) => a + b, 0);
    }}
    const CATEGORY_PALETTE = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf'];
    function categoryColor(categoryName) {{
      let hash = 0;
      for (let i = 0; i < categoryName.length; i += 1) {{
        hash = ((hash << 5) - hash) + categoryName.charCodeAt(i);
        hash |= 0;
      }}
      const idx = Math.abs(hash) % CATEGORY_PALETTE.length;
      return CATEGORY_PALETTE[idx];
    }}

    function renderKpis(k) {{
      const kpiEl = document.getElementById('kpis');
      kpiEl.innerHTML = '';
      [
        ['Period', `${{k.period_start || '-'}} to ${{k.period_end || '-'}}`],
        ['Total Revenue', euro.format(k.total_revenue || 0)],
        ['Number of Appointments', String(k.total_appointments || 0)],
        ['Average Monthly Revenue', euro.format(k.avg_monthly_revenue || 0)],
        ['Unique Customers', String(k.unique_customers || 0)],
        ['Average Appointment Value', euro.format(k.avg_appointment_value || 0)],
        ['Last Month MoM Growth', pct.format(k.last_month_mom_growth || 0)],
        ['Net Growth vs First Month', pct.format(k.net_growth_vs_first_month || 0)],
        ['Churn % (last month in range)', pct.format(k.dataset_churn_rate || 0)]
      ].forEach(([label, value]) => {{
        const div = document.createElement('div');
        div.className = 'kpi';
        div.innerHTML = `<div class="label">${{label}}</div><div class="value">${{value}}</div>`;
        kpiEl.appendChild(div);
      }});
    }}

    function monthStart(monthStr) {{
      return new Date(`${{monthStr}}-01T00:00:00`);
    }}

    function monthEnd(monthStr) {{
      const d = monthStart(monthStr);
      return new Date(d.getFullYear(), d.getMonth() + 1, 0);
    }}

    function computeChurnSeries(startIdx, endIdx, weeks) {{
      const events = (data.appointmentEvents || []).map(e => ({{
        customer: e.customer,
        date: new Date(`${{e.date}}T00:00:00`)
      }}));
      const eventsByCustomer = new Map();
      events.forEach((e) => {{
        if (!eventsByCustomer.has(e.customer)) eventsByCustomer.set(e.customer, []);
        eventsByCustomer.get(e.customer).push(e.date);
      }});
      eventsByCustomer.forEach((arr) => arr.sort((a, b) => a - b));

      const ELIGIBLE_LOOKBACK_DAYS = 90;
      const out = [];
      for (let idx = startIdx; idx <= endIdx; idx += 1) {{
        const mEnd = monthEnd(months[idx]);
        const eligibleStart = new Date(mEnd);
        eligibleStart.setDate(eligibleStart.getDate() - ELIGIBLE_LOOKBACK_DAYS);
        const winStart = new Date(mEnd);
        winStart.setDate(winStart.getDate() - weeks * 7);
        let inactive = 0;
        let eligible = 0;
        eventsByCustomer.forEach((dates) => {{
          const hadRecent = dates.some(d => d >= eligibleStart && d <= mEnd);
          if (!hadRecent) return;
          eligible += 1;
          const hadInTrailing = dates.some(d => d >= winStart && d <= mEnd);
          if (!hadInTrailing) inactive += 1;
        }});
        out.push({{
          churned_customers: inactive,
          churn_rate: eligible ? inactive / eligible : 0,
        }});
      }}
      return out;
    }}

    function renderCategoryPie(targetId, categoryTotals) {{
      const topCats = categoryTotals.slice(0, 8);
      const otherRevenue = sum(categoryTotals.slice(8).map(x => x.revenue));
      const pieLabels = topCats.map(x => x.name);
      const pieValues = topCats.map(x => x.revenue);
      if (otherRevenue > 0.001) {{
        pieLabels.push('Other');
        pieValues.push(otherRevenue);
      }}
      Plotly.newPlot(targetId, [{{
        type: 'pie',
        labels: pieLabels,
        values: pieValues,
        hole: 0.45,
        textinfo: 'percent',
        textposition: 'inside',
        insidetextorientation: 'auto',
        hovertemplate: '%{{label}}: %{{percent}} (%{{value:,.0f}} EUR)<extra></extra>',
        sort: false
      }}], {{
        margin: {{ t: 10, r: 10, b: 30, l: 10 }},
        paper_bgcolor: 'transparent',
        showlegend: true,
        legend: {{ orientation: 'h', y: -0.12 }},
        uniformtext: {{ minsize: 11, mode: 'hide' }}
      }}, {{ responsive: true }});
    }}

    function renderTables(categoryTotals, customerTotals, comparisonCategoryTotals, comparison) {{
      renderCategoryPie('topCategoriesPieCurrent', categoryTotals);
      if (comparison && comparison.available && comparisonCategoryTotals.length) {{
        document.getElementById('topCategoriesComparePanel').classList.remove('hidden');
        document.getElementById('topCategoriesPieCompareTitle').textContent =
          `Comparison (${{months[comparison.cStart]}} to ${{months[comparison.cEnd]}})`;
        renderCategoryPie('topCategoriesPieCompare', comparisonCategoryTotals);
      }} else {{
        document.getElementById('topCategoriesComparePanel').classList.add('hidden');
      }}

      const tcu = document.getElementById('topCustomersBody');
      tcu.innerHTML = '';
      customerTotals.slice(0, 12).forEach((r) => {{
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${{r.name}}</td><td>${{euro.format(r.revenue)}}</td>`;
        tcu.appendChild(tr);
      }});
    }}

    function getComparisonData(startIdx, endIdx, kpis) {{
      const mode = document.getElementById('comparisonMode').value;
      if (mode === 'none') return {{ mode, available: false, message: '' }};

      let cStart = -1;
      let cEnd = -1;
      if (mode === 'previous_period') {{
        const len = endIdx - startIdx + 1;
        cEnd = startIdx - 1;
        cStart = cEnd - len + 1;
      }} else if (mode === 'previous_year') {{
        cStart = startIdx - 12;
        cEnd = endIdx - 12;
      }} else if (mode === 'baseline_first') {{
        cStart = 0;
        cEnd = 0;
      }}

      if (cStart < 0 || cEnd < 0 || cStart >= months.length || cEnd >= months.length || cStart > cEnd) {{
        return {{ mode, available: false, message: 'Comparison period is not available for this selection.' }};
      }}

      const cmp = kpisForRange(cStart, cEnd);
      const revDelta = cmp.total_revenue ? (kpis.total_revenue - cmp.total_revenue) / cmp.total_revenue : 0;
      const churnDelta = kpis.dataset_churn_rate - cmp.dataset_churn_rate;
      const custDelta = kpis.unique_customers - cmp.unique_customers;
      return {{
        mode,
        available: true,
        cStart,
        cEnd,
        cmp,
        revDelta,
        churnDelta,
        custDelta,
      }};
    }}

    function renderExecutive(kpis, filteredNewBiz, filteredRecur, categoryTotals, filteredMonthly, comparison) {{
      const latestNewBiz = filteredNewBiz.slice(-1)[0] || {{}};
      const best = filteredMonthly.length ? filteredMonthly.reduce((a, b) => (b.revenue > a.revenue ? b : a)) : null;
      const worst = filteredMonthly.length ? filteredMonthly.reduce((a, b) => (b.revenue < a.revenue ? b : a)) : null;
      const latestChurn = filteredRecur.slice(-1)[0] || {{}};
      const topCat = categoryTotals[0] || null;
      if (comparison.mode === 'none') {{
        document.getElementById('execHeadline').textContent =
          `Revenue in selected period: ${{euro.format(kpis.total_revenue || 0)}}.`;
      }} else if (!comparison.available) {{
        document.getElementById('execHeadline').textContent = comparison.message;
      }} else {{
        document.getElementById('execHeadline').textContent =
          `Revenue is ${{pct.format(comparison.revDelta)}} vs selected comparison period.`;
      }}

      const highlights = [
        `Total revenue is ${{euro.format(kpis.total_revenue || 0)}} across ${{kpis.total_appointments || 0}} completed appointments.`,
        comparison.mode !== 'none' && comparison.available
          ? `Compared to ${{months[comparison.cStart]}} to ${{months[comparison.cEnd]}}, revenue change is ${{pct.format(comparison.revDelta)}} and customer change is ${{comparison.custDelta >= 0 ? '+' : ''}}${{comparison.custDelta}}.`
          : 'Select a comparison mode to see period-vs-period movement here.',
        best ? `Best month by revenue: ${{best.label}} at ${{euro.format(best.revenue)}}.` : 'No monthly data.',
        topCat ? `Top revenue category: ${{topCat.name}} at ${{euro.format(topCat.revenue)}}.` : 'No category data.',
        `Latest month new business: ${{latestNewBiz.new_customers || 0}} customers and ${{euro.format(latestNewBiz.new_business_revenue || 0)}}.`
      ];
      const watchouts = [
        `Latest month revenue: ${{euro.format(kpis.last_month_revenue || 0)}} (${{pct.format(kpis.last_month_mom_growth || 0)}} vs prior month).`,
        `Churn (last month in range): ${{latestChurn.churned_customers || 0}} customers (${{pct.format(latestChurn.churn_rate || 0)}}).`,
        worst ? `Lowest month by revenue: ${{worst.label}} at ${{euro.format(worst.revenue)}}.` : 'No low month identified.'
      ];

      const execHighlights = document.getElementById('execHighlights');
      execHighlights.innerHTML = '';
      highlights.forEach((txt) => {{
        const li = document.createElement('li');
        li.textContent = txt;
        execHighlights.appendChild(li);
      }});
      const execWatchouts = document.getElementById('execWatchouts');
      execWatchouts.innerHTML = '';
      watchouts.forEach((txt) => {{
        const li = document.createElement('li');
        li.textContent = txt;
        execWatchouts.appendChild(li);
      }});
      document.getElementById('pillRevenue').textContent = euro.format(kpis.last_month_revenue || 0);
      document.getElementById('pillNewCust').textContent = String(latestNewBiz.new_customers || 0);
      document.getElementById('pillChurn').textContent = pct.format(kpis.dataset_churn_rate || 0);
      document.getElementById('pillAov').textContent = euro.format(kpis.avg_appointment_value || 0);
    }}

    function kpisForRange(startIdx, endIdx) {{
      const labels = monthLabels.slice(startIdx, endIdx + 1);
      const filteredMonthly = (data.monthlyRevenue || []).slice(startIdx, endIdx + 1);
      const filteredRecur = (data.recurringAndChurn || []).slice(startIdx, endIdx + 1);
      const churnWeeks = Number(document.getElementById('churnWeeks').value || 4);
      const churnSeries = computeChurnSeries(startIdx, endIdx, churnWeeks);
      const totalRevenue = sum(filteredMonthly.map(x => x.revenue));
      const totalAppointments = Math.round((data.kpis.total_appointments || 0) * (totalRevenue / (data.kpis.total_revenue || 1)));
      const avgMonthlyRevenue = labels.length ? totalRevenue / labels.length : 0;
      const uniqueCustomers = new Set();
      (data.customerSeries || []).forEach((c) => {{
        if (sum(c.monthly.slice(startIdx, endIdx + 1)) > 0) uniqueCustomers.add(c.customer);
      }});
      const firstRevenue = filteredMonthly[0]?.revenue || 0;
      const lastRevenue = filteredMonthly.slice(-1)[0]?.revenue || 0;
      const prevRevenue = filteredMonthly.length > 1 ? filteredMonthly[filteredMonthly.length - 2].revenue : 0;
      return {{
        period_start: months[startIdx] || '-',
        period_end: months[endIdx] || '-',
        total_revenue: totalRevenue,
        avg_monthly_revenue: avgMonthlyRevenue,
        total_appointments: totalAppointments,
        avg_appointment_value: totalAppointments ? (totalRevenue / totalAppointments) : 0,
        unique_customers: uniqueCustomers.size,
        last_month_revenue: lastRevenue,
        last_month_mom_growth: prevRevenue ? ((lastRevenue - prevRevenue) / prevRevenue) : 0,
        net_growth_vs_first_month: firstRevenue ? ((lastRevenue - firstRevenue) / firstRevenue) : 0,
        dataset_churn_rate: churnSeries.length ? (churnSeries[churnSeries.length - 1].churn_rate || 0) : 0
      }};
    }}

    function renderComparison(startIdx, endIdx, kpis) {{
      const summary = document.getElementById('comparisonSummary');
      const comparison = getComparisonData(startIdx, endIdx, kpis);
      if (comparison.mode === 'none') {{
        summary.textContent = '';
        return comparison;
      }}
      if (!comparison.available) {{
        summary.textContent = comparison.message;
        return comparison;
      }}
      summary.textContent =
        `Comparison (${{months[comparison.cStart]}} to ${{months[comparison.cEnd]}}): Revenue ${{pct.format(comparison.revDelta)}} (${{euro.format(kpis.total_revenue)}} vs ${{euro.format(comparison.cmp.total_revenue)}}), ` +
        `Unique customers ${{comparison.custDelta >= 0 ? '+' : ''}}${{comparison.custDelta}}, Churn ${{pct.format(comparison.churnDelta)}}.`;
      return comparison;
    }}

    function renderForRange(startIdx, endIdx) {{
      const labels = monthLabels.slice(startIdx, endIdx + 1);
      const filteredMonthly = (data.monthlyRevenue || []).slice(startIdx, endIdx + 1);
      const filteredNewBiz = (data.newBusiness || []).slice(startIdx, endIdx + 1);
      const filteredRecurBase = (data.recurringAndChurn || []).slice(startIdx, endIdx + 1);
      const churnWeeks = Number(document.getElementById('churnWeeks').value || 4);
      const churnSeries = computeChurnSeries(startIdx, endIdx, churnWeeks);
      const filteredRecur = filteredRecurBase.map((r, i) => ({{
        ...r,
        churned_customers: churnSeries[i]?.churned_customers || 0,
        churn_rate: churnSeries[i]?.churn_rate || 0,
      }}));
      const filteredUnpaid = (data.unpaidImpact || []).slice(startIdx, endIdx + 1);

      const kpis = kpisForRange(startIdx, endIdx);
      renderKpis(kpis);

      const categoryTotals = (data.categorySeries || [])
        .map((c) => {{
          const revenue = sum(c.monthly.slice(startIdx, endIdx + 1));
          return {{ name: c.category, revenue, monthly: c.monthly.slice(startIdx, endIdx + 1) }};
        }})
        .filter(x => x.revenue > 0.001)
        .sort((a, b) => b.revenue - a.revenue);
      const monthlyCategoryTotals = labels.map((_, idx) =>
        sum(categoryTotals.map(c => c.monthly[idx] || 0))
      );

      const customerTotals = (data.customerSeries || [])
        .map((c) => {{
          const revenue = sum(c.monthly.slice(startIdx, endIdx + 1));
          return {{ name: c.customer_display || c.customer, revenue }};
        }})
        .filter(x => x.revenue > 0.001)
        .sort((a, b) => b.revenue - a.revenue);
      const comparison = renderComparison(startIdx, endIdx, kpis);
      let comparisonCategoryTotals = [];
      if (comparison && comparison.available) {{
        comparisonCategoryTotals = (data.categorySeries || [])
          .map((c) => {{
            const revenue = sum(c.monthly.slice(comparison.cStart, comparison.cEnd + 1));
            return {{ name: c.category, revenue, monthly: c.monthly.slice(comparison.cStart, comparison.cEnd + 1) }};
          }})
          .filter(x => x.revenue > 0.001)
          .sort((a, b) => b.revenue - a.revenue);
      }}
      renderTables(categoryTotals, customerTotals, comparisonCategoryTotals, comparison);
      renderExecutive(kpis, filteredNewBiz, filteredRecur, categoryTotals, filteredMonthly, comparison);
      const unpaidVolume = sum(filteredUnpaid.map(x => x.unpaid_count || 0));
      const unpaidRevenue = sum(filteredUnpaid.map(x => x.unpaid_revenue || 0));
      document.getElementById('unpaidVolumeValue').textContent = String(unpaidVolume);
      document.getElementById('unpaidRevenueValue').textContent = euro.format(unpaidRevenue);

      if (comparison && comparison.available) {{
        const comparisonLabels = months.slice(comparison.cStart, comparison.cEnd + 1).map(
          (m, idx) => data.monthLabels[comparison.cStart + idx] || m
        );
        const alignedComparisonSeriesRaw = (data.categorySeries || []).map((c) => {{
          const y = c.monthly.slice(comparison.cStart, comparison.cEnd + 1);
          return {{ name: c.category, monthly: y }};
        }});
        const comparisonByCategory = new Map(alignedComparisonSeriesRaw.map(c => [c.name, c]));
        const alignedComparisonSeries = categoryTotals.map((c) => {{
          const found = comparisonByCategory.get(c.name);
          return found || {{ name: c.name, monthly: labels.map(() => 0) }};
        }});
        const baselineX = labels.map((_, i) => i * 3);
        const selectedX = labels.map((_, i) => i * 3 + 1);
        const tickVals = labels.map((_, i) => i * 3 + 0.5);
        const tickText = labels.map((l, i) => `${{comparisonLabels[i] || 'n/a'}} vs ${{l}}`);

        Plotly.newPlot('categoryRevenueChart', [
          ...categoryTotals.map((c) => ({{
            type: 'bar',
            name: c.name,
            x: selectedX,
            y: c.monthly,
            marker: {{ color: categoryColor(c.name), opacity: 0.88 }},
            legendgroup: c.name,
            hovertemplate: `${{c.name}} (Selected): %{{y:,.0f}} EUR<extra></extra>`
          }})),
          ...alignedComparisonSeries.map((c) => ({{
            type: 'bar',
            name: c.name,
            x: baselineX,
            y: c.monthly,
            marker: {{ color: categoryColor(c.name), opacity: 0.45 }},
            legendgroup: c.name,
            showlegend: false,
            hovertemplate: `${{c.name}} (Baseline): %{{y:,.0f}} EUR<extra></extra>`
          }}))
        ], {{
          barmode: 'stack',
          bargap: 0.35,
          bargroupgap: 0.05,
          margin: {{ t: 20, r: 20, b: 70, l: 60 }},
          paper_bgcolor: 'transparent', plot_bgcolor: '#fff',
          yaxis: {{ title: 'Revenue (€)', gridcolor: '#e8f0ed', rangemode: 'tozero' }},
          xaxis: {{
            title: 'Selected month vs comparison month',
            tickmode: 'array',
            tickvals: tickVals,
            ticktext: tickText,
            tickangle: -15
          }},
          legend: {{ orientation: 'h', y: -0.4 }}
        }}, {{ responsive: true }});
      }} else {{
        Plotly.newPlot('categoryRevenueChart', [
          ...categoryTotals.map((c) => ({{
          type: 'bar', name: c.name, x: labels, y: c.monthly, marker: {{ opacity: 0.9 }}
          }})),
          {{
            type: 'scatter',
            mode: 'lines+markers+text',
            name: 'Monthly Total',
            x: labels,
            y: monthlyCategoryTotals,
            text: monthlyCategoryTotals.map(v => euro.format(v)),
            textposition: 'top center',
            textfont: {{ size: 12, color: '#2a2a2a' }},
            marker: {{ color: '#2a2a2a', size: 8 }},
            line: {{ color: '#2a2a2a', width: 3 }},
            hovertemplate: 'Total: %{{text}}<extra></extra>'
          }}
        ], {{
          barmode: 'stack', margin: {{ t: 20, r: 20, b: 50, l: 60 }},
          paper_bgcolor: 'transparent', plot_bgcolor: '#fff',
          yaxis: {{ title: 'Revenue (€)', gridcolor: '#e8f0ed', rangemode: 'tozero' }}, xaxis: {{ title: 'Month' }},
          legend: {{ orientation: 'h', y: -0.35 }}
        }}, {{ responsive: true }});
      }}

      Plotly.newPlot('newBusinessChart', [
        {{ type: 'bar', name: 'New Business Revenue', x: labels, y: filteredNewBiz.map(x => x.new_business_revenue), marker: {{ color: '#56b665' }} }},
        {{ type: 'scatter', mode: 'lines+markers', name: 'New Customers', x: labels, y: filteredNewBiz.map(x => x.new_customers), yaxis: 'y2', line: {{ color: '#6dab3c', width: 3 }} }}
      ], {{
        margin: {{ t: 20, r: 40, b: 50, l: 60 }}, paper_bgcolor: 'transparent', plot_bgcolor: '#fff',
        yaxis: {{ title: 'Revenue (€)', gridcolor: '#e8f0ed', rangemode: 'tozero' }}, yaxis2: {{ title: 'Customers', overlaying: 'y', side: 'right', rangemode: 'tozero' }},
        xaxis: {{ title: 'Month' }}
      }}, {{ responsive: true }});

      Plotly.newPlot('recurringChurnChart', [
        {{ type: 'bar', name: 'Recurring Revenue', x: labels, y: filteredRecur.map(x => x.recurring_revenue), marker: {{ color: '#75d69c' }} }},
        {{ type: 'scatter', mode: 'lines+markers', name: 'Churned Customers', x: labels, y: filteredRecur.map(x => x.churned_customers), yaxis: 'y2', line: {{ color: '#c94040', width: 3 }} }},
        {{ type: 'scatter', mode: 'lines+markers', name: 'Churn Rate', x: labels, y: filteredRecur.map(x => x.churn_rate * 100), yaxis: 'y3', line: {{ color: '#6dab3c', width: 2, dash: 'dot' }} }}
      ], {{
        margin: {{ t: 20, r: 70, b: 50, l: 60 }}, paper_bgcolor: 'transparent', plot_bgcolor: '#fff',
        yaxis: {{ title: 'Revenue (€)', gridcolor: '#e8f0ed', rangemode: 'tozero' }},
        yaxis2: {{ title: 'Customers', overlaying: 'y', side: 'right', rangemode: 'tozero' }},
        yaxis3: {{ title: 'Churn %', overlaying: 'y', side: 'right', position: 0.95, rangemode: 'tozero' }},
        xaxis: {{ title: 'Month' }}
      }}, {{ responsive: true }});

      Plotly.newPlot('monthlyTrendChart', [
        {{ type: 'scatter', mode: 'lines+markers', name: 'Revenue', x: labels, y: filteredMonthly.map(x => x.revenue), line: {{ width: 3, color: '#56b665' }} }},
        {{ type: 'scatter', mode: 'lines+markers', name: 'Active Customers', x: labels, y: filteredMonthly.map(x => x.active_customers), yaxis: 'y2', line: {{ width: 2, color: '#4f6f66' }} }}
      ], {{
        margin: {{ t: 20, r: 50, b: 50, l: 60 }}, paper_bgcolor: 'transparent', plot_bgcolor: '#fff',
        yaxis: {{ title: 'Revenue (€)', gridcolor: '#e8f0ed', rangemode: 'tozero' }},
        yaxis2: {{ title: 'Customers', overlaying: 'y', side: 'right', rangemode: 'tozero' }}, xaxis: {{ title: 'Month' }}
      }}, {{ responsive: true }});

      Plotly.newPlot('unpaidImpactChart', [
        {{ type: 'bar', name: 'Unpaid Revenue', x: labels, y: filteredUnpaid.map(x => x.unpaid_revenue), marker: {{ color: '#c94040' }} }},
        {{ type: 'scatter', mode: 'lines+markers', name: 'Unpaid Appointments', x: labels, y: filteredUnpaid.map(x => x.unpaid_count), yaxis: 'y2', line: {{ width: 3, color: '#2a2a2a' }} }}
      ], {{
        margin: {{ t: 20, r: 50, b: 50, l: 60 }}, paper_bgcolor: 'transparent', plot_bgcolor: '#fff',
        yaxis: {{ title: 'Revenue (€)', gridcolor: '#e8f0ed', rangemode: 'tozero' }},
        yaxis2: {{ title: 'Appointments', overlaying: 'y', side: 'right', rangemode: 'tozero' }},
        xaxis: {{ title: 'Month' }}
      }}, {{ responsive: true }});
    }}

    const startMonthEl = document.getElementById('startMonth');
    const endMonthEl = document.getElementById('endMonth');
    const comparisonModeEl = document.getElementById('comparisonMode');
    const includeUnpaidEl = document.getElementById('includeUnpaid');
    const churnWeeksEl = document.getElementById('churnWeeks');

    function refreshMonthSelectors() {{
      const prevStart = Number(startMonthEl.value || 0);
      const prevEnd = Number(endMonthEl.value || Math.max(0, months.length - 1));
      startMonthEl.innerHTML = '';
      endMonthEl.innerHTML = '';
      months.forEach((m, idx) => {{
        const label = monthLabels[idx] || m;
        startMonthEl.innerHTML += `<option value="${{idx}}">${{label}}</option>`;
        endMonthEl.innerHTML += `<option value="${{idx}}">${{label}}</option>`;
      }});
      const maxIdx = Math.max(0, months.length - 1);
      startMonthEl.value = String(Math.min(prevStart, maxIdx));
      endMonthEl.value = String(Math.min(Math.max(prevEnd, Number(startMonthEl.value)), maxIdx));
    }}

    function onFilterChange(evt) {{
      let s = Number(startMonthEl.value);
      let e = Number(endMonthEl.value);
      if (s > e) {{
        if (evt && evt.target === startMonthEl) endMonthEl.value = String(s);
        else startMonthEl.value = String(e);
        s = Number(startMonthEl.value);
        e = Number(endMonthEl.value);
      }}
      renderForRange(s, e);
    }}
    startMonthEl.addEventListener('change', onFilterChange);
    endMonthEl.addEventListener('change', onFilterChange);
    comparisonModeEl.addEventListener('change', onFilterChange);
    churnWeeksEl.addEventListener('change', onFilterChange);
    includeUnpaidEl.addEventListener('change', () => {{
      data = includeUnpaidEl.value === 'yes' ? datasets.with_unpaid : datasets.paid_only;
      months = data.months || [];
      monthLabels = data.monthLabels || [];
      refreshMonthSelectors();
      onFilterChange();
    }});

    function applyRange(s, e) {{
      startMonthEl.value = String(Math.max(0, s));
      endMonthEl.value = String(Math.max(0, e));
      onFilterChange();
    }}
    document.getElementById('presetAll').addEventListener('click', () => applyRange(0, months.length - 1));
    document.getElementById('presetLast3').addEventListener('click', () => applyRange(Math.max(0, months.length - 3), months.length - 1));
    document.getElementById('presetLast6').addEventListener('click', () => applyRange(Math.max(0, months.length - 6), months.length - 1));
    document.getElementById('presetYtd').addEventListener('click', () => {{
      if (!months.length) return;
      const lastYear = Number((months[months.length - 1] || '0-00').split('-')[0]);
      let firstIdx = months.findIndex(m => Number(m.split('-')[0]) === lastYear);
      if (firstIdx < 0) firstIdx = 0;
      applyRange(firstIdx, months.length - 1);
    }});
    refreshMonthSelectors();
    renderForRange(0, Math.max(0, months.length - 1));

    function updateDefinitions() {{
      const churnWeeks = Number(document.getElementById('churnWeeks').value || 4);
      document.getElementById('definitions').textContent =
        `Definitions: New business = ${{data.definitions.new_business}} | ` +
        `Recurring business = ${{data.definitions.recurring_business}} | ` +
        `Churn = At each month-end, among customers with any appointment in the prior 90 days: those with no appointment in the trailing ${{churnWeeks}} weeks; rate = inactive ÷ that 90-day engaged base. Longer week window → lower rate.`;
    }}
    updateDefinitions();
    churnWeeksEl.addEventListener('change', updateDefinitions);
  </script>
</body>
</html>
"""


def main() -> None:
    args = parse_args()
    csv_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    if not csv_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {csv_path}")

    date_overrides = load_date_overrides(args.date_overrides)
    appointments = read_appointments(csv_path, date_overrides=date_overrides)
    appointments_paid_only = [a for a in appointments if a.get("paid")]
    metrics_with_unpaid = build_metrics(appointments)
    metrics_paid_only = build_metrics(appointments_paid_only)
    html = render_html(metrics_with_unpaid, metrics_paid_only)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")

    print(f"Dashboard written: {output_path}")
    print(f"Appointments included: {len(appointments)}")
    if metrics_with_unpaid.get("months"):
        print(f"Period: {metrics_with_unpaid['months'][0]} -> {metrics_with_unpaid['months'][-1]}")


if __name__ == "__main__":
    main()
