from __future__ import annotations

from datetime import datetime
from pathlib import Path
import html
import webbrowser

from .models import Listing


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "k.A."
    return f"CHF {value:,.0f}".replace(",", "'")


def _fmt_num(value: float | None, suffix: str) -> str:
    if value is None:
        return "k.A."
    as_int = int(value)
    return f"{as_int} {suffix}" if as_int == value else f"{value:.1f} {suffix}"


def _ppsqm(price: float | None, area: float | None) -> float | None:
    if not price or not area or area <= 0:
        return None
    return price / area


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    sorted_values = sorted(values)
    n = len(sorted_values)
    mid = n // 2
    if n % 2 == 1:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2.0


def _row_valuation(ppsqm: float | None, median_ppsqm: float | None) -> str:
    if ppsqm is None or median_ppsqm is None or median_ppsqm <= 0:
        return "unklar"
    ratio = ppsqm / median_ppsqm
    if ratio <= 0.90:
        return "eher guenstig"
    if ratio >= 1.10:
        return "eher teuer"
    return "mittel"


def _build_table_rows(items: list[Listing], median_ppsqm: float | None) -> str:
    rows: list[str] = []
    for item in items:
        ppsqm = _ppsqm(item.price_chf, item.area_sqm)
        valuation = _row_valuation(ppsqm, median_ppsqm)
        if ppsqm:
            ppsqm_fmt = format(ppsqm, ",.0f").replace(",", "'")
            ppsqm_str = f"CHF {ppsqm_fmt}/qm"
        else:
            ppsqm_str = "k.A."
        rows.append(
            "<tr>"
            f"<td>{html.escape(item.category)}</td>"
            f"<td>{html.escape(item.title)}</td>"
            f"<td>{html.escape(item.address or 'k.A.')}</td>"
            f"<td>{_fmt_price(item.price_chf)}</td>"
            f"<td>{_fmt_num(item.area_sqm, 'qm')}</td>"
            f"<td>{_fmt_num(item.rooms, 'Zi')}</td>"
            f"<td>{ppsqm_str}</td>"
            f"<td>{html.escape(valuation)}</td>"
            f"<td><a href=\"{html.escape(item.url)}\" target=\"_blank\">Link</a></td>"
            "</tr>"
        )
    return "\n".join(rows)


def write_dashboard_html(
    output_path: str,
    new_items: list[Listing],
    recent_history: list[dict[str, object]],
    table_title: str = "Neue Treffer (letzter Lauf)",
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    recent_ppsqm = [
        float(row["price_chf"]) / float(row["area_sqm"])
        for row in recent_history
        if row.get("price_chf") and row.get("area_sqm") and float(row["area_sqm"]) > 0
    ]
    median_ppsqm = _median(recent_ppsqm)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_body = f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Hausfinder Dashboard</title>
  <style>
    :root {{
      --bg: #f3f1eb;
      --ink: #1d232b;
      --card: #fffdf8;
      --accent: #0f766e;
      --muted: #5f6773;
    }}
    body {{ margin: 0; font-family: "Avenir Next", "Segoe UI", sans-serif; color: var(--ink); background: radial-gradient(circle at 10% 5%, #fff4de, var(--bg)); }}
    .wrap {{ max-width: 1100px; margin: 2rem auto; padding: 0 1rem; }}
    .card {{ background: var(--card); border-radius: 14px; box-shadow: 0 8px 30px rgba(0,0,0,0.08); padding: 1rem 1.2rem; margin-bottom: 1rem; }}
    h1, h2 {{ margin: 0 0 0.6rem 0; }}
    .meta {{ color: var(--muted); }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.95rem; }}
    th, td {{ border-bottom: 1px solid #e4e2dc; padding: 0.55rem; text-align: left; vertical-align: top; }}
    th {{ background: #f1efe7; position: sticky; top: 0; }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>Hausfinder Dashboard</h1>
      <div class="meta">Stand: {html.escape(now)} | Neue Treffer: {len(new_items)} | Historie: {len(recent_history)} | Median CHF/qm: {("CHF " + format(median_ppsqm, ",.0f").replace(",", "'")) if median_ppsqm else "k.A."}</div>
    </div>
    <div class="card">
      <h2>{html.escape(table_title)}</h2>
      <table>
        <thead>
          <tr><th>Typ</th><th>Titel</th><th>Adresse</th><th>Preis</th><th>Flaeche</th><th>Zimmer</th><th>CHF/qm</th><th>Bewertung</th><th>Quelle</th></tr>
        </thead>
        <tbody>
          {_build_table_rows(new_items, median_ppsqm)}
        </tbody>
      </table>
    </div>
  </div>
</body>
</html>
"""
    path.write_text(html_body, encoding="utf-8")
    return path


def open_dashboard(path: Path) -> None:
    webbrowser.open(path.resolve().as_uri())
