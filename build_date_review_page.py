#!/usr/bin/env python3
"""
Build a manual date QA page to review suspicious appointment dates.

Usage:
  python3 build_date_review_page.py \
    --input "input_data/your_export.csv" \
    --output "date_review.html"
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path
from typing import Dict, List


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a date QA review page for ZenMaid export.")
    parser.add_argument("--input", required=True, help="Path to ZenMaid appointment CSV export.")
    parser.add_argument("--output", default="date_review.html", help="Output HTML path.")
    return parser.parse_args()


def to_float(raw: str) -> float:
    txt = str(raw or "").strip().replace(",", "")
    if not txt:
        return 0.0
    try:
        return float(txt)
    except ValueError:
        return 0.0


def parse_dmy(date_text: str) -> dt.date | None:
    try:
        return dt.datetime.strptime(date_text, "%d/%m/%Y").date()
    except ValueError:
        return None


def parse_mdy(date_text: str) -> dt.date | None:
    try:
        return dt.datetime.strptime(date_text, "%m/%d/%Y").date()
    except ValueError:
        return None


def to_iso(d: dt.date | None) -> str:
    return d.isoformat() if d else ""


def appointment_revenue(row: Dict[str, str]) -> float:
    # Net/base revenue only (exclude tax).
    return to_float(row.get("Price", "0"))


def customer_label(row: Dict[str, str]) -> str:
    return (
        (row.get("Customer Full Name") or "").strip()
        or (row.get("Customer Company Name") or "").strip()
        or (row.get("Customer Emails") or row.get("Customer Email 1") or "").strip()
        or ((row.get("Customer ID") or "").strip())
    )


def classify_date(raw: str) -> str:
    parts = raw.split("/")
    if len(parts) != 3:
        return "other"
    try:
        first = int(parts[0])
        second = int(parts[1])
    except ValueError:
        return "other"
    if first <= 12 and second <= 12:
        return "ambiguous"
    if first <= 12 and second > 12:
        return "likely_mmdd"
    if first > 12 and second <= 12:
        return "likely_ddmm"
    return "other"


def collect_suspicious_rows(csv_path: Path) -> List[Dict]:
    rows: List[Dict] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        _ = handle.readline()
        reader = csv.DictReader(handle)
        for line_num, row in enumerate(reader, start=3):
            raw_date = (row.get("Appointment Date") or "").strip()
            if not raw_date or "/" not in raw_date:
                continue

            kind = classify_date(raw_date)
            if kind not in {"ambiguous", "likely_mmdd"}:
                continue

            dmy = parse_dmy(raw_date)
            mdy = parse_mdy(raw_date)
            if kind == "ambiguous" and not (dmy and mdy):
                continue
            if kind == "likely_mmdd" and not mdy:
                continue

            appt_id = (row.get("Appointment ID") or "").strip()
            status = (row.get("Appointment Status") or "").strip()
            status_l = status.lower()
            if status_l in {"cancelled", "holiday calendar", "stand-by", "stand by"}:
                continue
            revenue = appointment_revenue(row)
            if revenue < 0:
                continue
            default_choice = "mdy" if kind == "likely_mmdd" else "dmy"
            rows.append(
                {
                    "line": line_num,
                    "appointment_id": appt_id,
                    "status": status,
                    "customer": customer_label(row),
                    "service_type": (row.get("Service Type") or "").strip(),
                    "raw_date": raw_date,
                    "kind": kind,
                    "dmy_iso": to_iso(dmy),
                    "mdy_iso": to_iso(mdy),
                    "default_choice": default_choice,
                    "revenue": round(revenue, 2),
                }
            )
    return rows


def render_html(rows: List[Dict], src: str) -> str:
    payload = json.dumps(rows)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>C&N Date QA Review</title>
  <style>
    :root {{
      --bg: #f5faf5;
      --card: #ffffff;
      --ink: #2a2a2a;
      --sub: #5a5a5a;
      --brand: #56b665;
      --brand-2: #75d69c;
      --warn: #d98b14;
      --line: #dce9d9;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: "Avenir Next", "Segoe UI", Arial, sans-serif; background: var(--bg); color: var(--ink); }}
    .wrap {{ max-width: 1500px; margin: 0 auto; padding: 20px; }}
    .hero {{
      background: linear-gradient(135deg, var(--brand), var(--brand-2));
      color: #fff;
      border-radius: 16px;
      padding: 18px;
    }}
    .hero h1 {{ margin: 0; font-size: 28px; }}
    .hero p {{ margin: 8px 0 0; opacity: 0.95; }}
    .section {{
      margin-top: 14px;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 14px;
      background: var(--card);
    }}
    .toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }}
    .toolbar label {{ font-size: 12px; text-transform: uppercase; color: var(--sub); letter-spacing: 0.06em; }}
    .toolbar input, .toolbar select, .toolbar button {{
      border: 1px solid #cfe2ce;
      border-radius: 10px;
      padding: 8px 10px;
      background: #fff;
      color: var(--ink);
      font-size: 14px;
    }}
    .toolbar button {{
      background: #eef8ef;
      color: #2f6d3d;
      font-weight: 700;
      cursor: pointer;
    }}
    .toolbar button:hover {{ background: #e3f3e6; }}
    .stats {{ margin-top: 10px; color: var(--sub); font-size: 13px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 8px; text-align: left; vertical-align: top; }}
    th {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--sub); }}
    .badge {{ display: inline-block; border-radius: 999px; padding: 2px 8px; font-size: 11px; font-weight: 700; }}
    .kind-ambiguous {{ background: #fff3dc; color: #7d4f07; }}
    .kind-likely_mmdd {{ background: #ffe5e5; color: #8b2222; }}
    .small {{ color: var(--sub); font-size: 12px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>C&N Date QA Review</h1>
      <p>Source: {src}</p>
      <p>Review suspicious date rows, set the correct interpretation, then export overrides.</p>
    </section>

    <section class="section">
      <div class="toolbar">
        <div>
          <label for="kindFilter">Filter</label><br />
          <select id="kindFilter">
            <option value="all">All suspicious</option>
            <option value="ambiguous">Ambiguous (01-12/01-12)</option>
            <option value="likely_mmdd">Likely US format (mm/dd)</option>
          </select>
        </div>
        <div>
          <label for="searchInput">Search</label><br />
          <input id="searchInput" placeholder="Appointment ID / customer / date" />
        </div>
        <div>
          <label>&nbsp;</label><br />
          <button id="downloadOverrides" type="button">Download overrides JSON</button>
        </div>
      </div>
      <div class="stats" id="stats"></div>
      <div style="overflow:auto;">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Raw Date</th>
              <th>Type</th>
              <th>DD/MM -> ISO</th>
              <th>MM/DD -> ISO</th>
              <th>Decision</th>
              <th>Manual ISO</th>
              <th>Status</th>
              <th>Customer</th>
              <th>Service</th>
              <th>Revenue</th>
            </tr>
          </thead>
          <tbody id="rows"></tbody>
        </table>
      </div>
      <p class="small">Decision rules: choose <strong>Use DD/MM</strong>, <strong>Use MM/DD</strong>, or <strong>Manual</strong>. Overrides are exported as appointment ID -> corrected date (`YYYY-MM-DD`).</p>
    </section>
  </div>

  <script>
    const data = {payload};
    const euro = new Intl.NumberFormat('en-IE', {{ style: 'currency', currency: 'EUR', maximumFractionDigits: 0 }});
    const rowsEl = document.getElementById('rows');
    const statsEl = document.getElementById('stats');
    const filterEl = document.getElementById('kindFilter');
    const searchEl = document.getElementById('searchInput');

    const state = new Map();
    data.forEach((r) => state.set(r.appointment_id, {{
      decision: r.default_choice,
      manual_iso: r.default_choice === 'dmy' ? r.dmy_iso : (r.mdy_iso || '')
    }}));

    function selectedIso(r) {{
      const s = state.get(r.appointment_id);
      if (!s) return '';
      if (s.decision === 'dmy') return r.dmy_iso;
      if (s.decision === 'mdy') return r.mdy_iso;
      return (s.manual_iso || '').trim();
    }}

    function render() {{
      const kind = filterEl.value;
      const q = searchEl.value.trim().toLowerCase();
      rowsEl.innerHTML = '';

      const filtered = data.filter((r) => {{
        if (kind !== 'all' && r.kind !== kind) return false;
        if (!q) return true;
        const hay = `${{r.appointment_id}} ${{r.customer}} ${{r.raw_date}} ${{r.service_type}}`.toLowerCase();
        return hay.includes(q);
      }});

      filtered.forEach((r) => {{
        const st = state.get(r.appointment_id);
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td>${{r.appointment_id}}<div class="small">line ${{r.line}}</div></td>
          <td>${{r.raw_date}}</td>
          <td><span class="badge kind-${{r.kind}}">${{r.kind}}</span></td>
          <td>${{r.dmy_iso || '-'}}</td>
          <td>${{r.mdy_iso || '-'}}</td>
          <td>
            <select data-id="${{r.appointment_id}}" class="decision">
              <option value="dmy">Use DD/MM</option>
              <option value="mdy">Use MM/DD</option>
              <option value="manual">Manual</option>
            </select>
          </td>
          <td><input data-id="${{r.appointment_id}}" class="manual" type="date" value="${{st.manual_iso || ''}}" /></td>
          <td>${{r.status}}</td>
          <td>${{r.customer || '-'}}</td>
          <td>${{r.service_type || '-'}}</td>
          <td>${{euro.format(r.revenue || 0)}}</td>
        `;
        rowsEl.appendChild(tr);

        const sel = tr.querySelector('.decision');
        sel.value = st.decision;
        const manual = tr.querySelector('.manual');
        manual.disabled = st.decision !== 'manual';
      }});

      const overridesCount = data.filter((r) => {{
        const sel = selectedIso(r);
        return sel && sel !== r.dmy_iso;
      }}).length;
      statsEl.textContent = `Rows shown: ${{filtered.length}} / ${{data.length}} | Overrides different from DD/MM: ${{overridesCount}}`;
    }}

    rowsEl.addEventListener('change', (evt) => {{
      const id = evt.target.getAttribute('data-id');
      if (!id || !state.has(id)) return;
      const st = state.get(id);
      if (evt.target.classList.contains('decision')) {{
        st.decision = evt.target.value;
        const tr = evt.target.closest('tr');
        const manual = tr.querySelector('.manual');
        manual.disabled = st.decision !== 'manual';
      }} else if (evt.target.classList.contains('manual')) {{
        st.manual_iso = evt.target.value;
      }}
      state.set(id, st);
      render();
    }});

    filterEl.addEventListener('change', render);
    searchEl.addEventListener('input', render);

    document.getElementById('downloadOverrides').addEventListener('click', () => {{
      const out = {{}};
      data.forEach((r) => {{
        const iso = selectedIso(r);
        if (!iso) return;
        if (iso !== r.dmy_iso) out[r.appointment_id] = iso;
      }});
      const blob = new Blob([JSON.stringify(out, null, 2)], {{ type: 'application/json' }});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'date_overrides.json';
      a.click();
      URL.revokeObjectURL(url);
    }});

    render();
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
    rows = collect_suspicious_rows(csv_path)
    html = render_html(rows, str(csv_path))
    output_path.write_text(html, encoding="utf-8")
    print(f"Date review page written: {output_path}")
    print(f"Suspicious rows: {len(rows)}")


if __name__ == "__main__":
    main()
