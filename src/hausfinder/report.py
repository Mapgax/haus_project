from __future__ import annotations

from datetime import datetime

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


def _line(item: Listing) -> str:
    return (
        f"- {item.title}\n"
        f"  Adresse: {item.address or 'k.A.'}\n"
        f"  Groesse: {_fmt_num(item.area_sqm, 'qm')} | Zimmer: {_fmt_num(item.rooms, 'Zi')}\n"
        f"  Preis: {_fmt_price(item.price_chf)}\n"
        f"  Quelle: {item.source_name}\n"
        f"  Link: {item.url}"
    )


def build_email_text(buy: list[Listing], rent: list[Listing]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"Hausfinder Report ({now})", ""]

    lines.append("KAUF")
    if buy:
        lines.extend(_line(item) for item in buy)
    else:
        lines.append("- Keine neuen passenden Kaufobjekte gefunden.")
    lines.append("")

    lines.append("MIETE")
    if rent:
        lines.extend(_line(item) for item in rent)
    else:
        lines.append("- Keine neuen passenden Mietobjekte gefunden.")
    lines.append("")

    lines.append("Hinweis: Automatisch gesammelt; bitte jedes Objekt manuell pruefen.")
    return "\n".join(lines)

