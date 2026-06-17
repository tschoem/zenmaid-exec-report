# Zenmaid Board Sales Dashboard

This folder includes a repeatable dashboard generator for board meetings.

## Generate the dashboard

Run from this folder:

```bash
python3 build_board_dashboard.py \
  --input "input_data/<new_export_file>.csv" \
  --output "board_dashboard.html"
```

Then open `board_dashboard.html` in a browser.

### Optional: use manual date overrides

If you reviewed suspicious dates and exported `date_overrides.json`, include it:

```bash
python3 build_board_dashboard.py \
  --input "input_data/<new_export_file>.csv" \
  --date-overrides "date_overrides.json" \
  --output "board_dashboard.html"
```

## Date QA review page (manual confirm/modify)

Generate a page that flags potentially misformatted dates and lets you export corrections:

```bash
python3 build_date_review_page.py \
  --input "input_data/<new_export_file>.csv" \
  --output "date_review.html"
```

Open `date_review.html`, review rows, and click **Download overrides JSON**.

## What it shows

- Revenue per category (appointment type) per month
- New business per month:
  - Revenue
  - Number of new customers
- Recurring business and churn:
  - Recurring revenue
  - Churned customers by month
  - Churn rate
- Board-style summary KPIs:
  - Total revenue, average monthly revenue
  - Unique customers, average appointment value
  - Month-over-month growth
  - Net growth vs first month
  - Overall churn rate in the period
- Top categories and top customers by revenue

## Metric definitions used

- New business: customers in their first completed month in the dataset.
- New business revenue: all completed revenue from those customers in that first month.
- Recurring business: revenue from customers whose first completed month was before the current month.
- Churn: customers active in prior month but inactive in current month.

