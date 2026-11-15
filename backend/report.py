"""PDF report of a unit (or one handover): summary, floor plan with pins, findings with photos,
places, timelines and record hashes ("Export PDF" / "Add to PDF report")."""

import io
from datetime import UTC, datetime

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from inspection import floorplan
from inspection.render import COLORS

from .schemas import DashboardOut, IssueOut, MapOut
from .storage import storage

EVENT_LABELS = {"move_in": "Move-in", "move_out": "Move-out", "repair": "Repair",
                "tenant_report": "Tenant report", "inspection": "Inspection"}


def _img(key: str | None, max_w: float, max_h: float):
    if not key:
        return ""
    try:
        data = storage.local_path(key).read_bytes()
        w, h = PILImage.open(io.BytesIO(data)).size
    except Exception:
        return ""
    s = min(max_w / w, max_h / h)
    return Image(io.BytesIO(data), width=w * s, height=h * s)


def _key_from_url(url: str | None) -> str | None:
    if not url:
        return None
    return url.split("/files/", 1)[1].split("?", 1)[0] if "/files/" in url else None


def build(m: MapOut, map_row, issues: list[IssueOut], dash: DashboardOut, title_suffix: str = "") -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm, title=f"{m.name} report")
    st = getSampleStyleSheet()
    small = st["BodyText"].clone("small", fontSize=8, leading=10)
    story = [
        Paragraph(f"{m.name}{title_suffix}", st["Title"]),
        Paragraph(" · ".join(x for x in [m.property, m.floor_label,
                                          f"scanned {m.scanned_at:%d %b %Y}" if m.scanned_at else None] if x), st["Normal"]),
        Paragraph(f"Report generated {datetime.now(UTC):%d %b %Y %H:%M} UTC", small),
        Spacer(1, 6 * mm),
    ]
    summary = Table([["Open findings", "Closed this year", "Days to repair (avg)", "Records"],
                     [dash.open_findings, dash.closed_this_year,
                      "-" if dash.days_to_repair_avg is None else dash.days_to_repair_avg, dash.records]],
                    colWidths=[45 * mm] * 4, rowHeights=[6 * mm, 11 * mm])
    summary.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, 0), 8), ("FONTSIZE", (0, 1), (-1, 1), 16),
                                 ("LEADING", (0, 1), (-1, 1), 18), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                 ("TEXTCOLOR", (0, 0), (-1, 0), colors.grey),
                                 ("BOX", (0, 0), (-1, -1), 0.5, colors.lightgrey)]))
    story += [summary, Spacer(1, 6 * mm)]

    if map_row.floor_plan_key and map_row.floor_plan:
        pins = [(i.floor_xy, COLORS.get(i.type, (255, 0, 0))) for i in issues if i.floor_xy]
        png = floorplan.with_pins(storage.local_path(map_row.floor_plan_key).read_bytes(), map_row.floor_plan, pins)
        w, h = PILImage.open(io.BytesIO(png)).size
        s = min(180 * mm / w, 110 * mm / h)
        story += [Image(io.BytesIO(png), width=w * s, height=h * s), Spacer(1, 6 * mm)]

    rows = [["Photo", "Finding", "Place", "Status", "Timeline", "Evidence"]]
    for i in issues:
        last = i.observations[-1] if i.observations else None
        timeline = "<br/>".join(
            f"{o.timestamp:%d.%m.%Y} {EVENT_LABELS.get(o.event_kind, o.event_kind or '')} "
            f"{o.recorder or ''}".strip() for o in i.observations[-4:])
        size = ""
        if i.size is not None:
            size = f"{i.size:.2f} m" if i.type in ("crack", "scratch") else f"{i.size:.3f} m²"
        show_h = i.height_m is not None and i.surface not in ("floor", None)
        place = i.place + (f"<br/>{i.height_m:.1f} m above floor" if show_h else "")
        rows.append([
            _img(_key_from_url(last.overlay_url or last.image_url) if last else None, 30 * mm, 30 * mm),
            Paragraph(f"<b>{i.label}</b> {i.issue_id}<br/>{i.severity} {size}", small),
            Paragraph(place, small),
            Paragraph(i.status + (f"<br/>repaired in {i.days_to_repair:g} d" if i.days_to_repair is not None else ""), small),
            Paragraph(timeline or "-", small),
            Paragraph(f"<font size=6>{(last.record_hash or '')[:16]}…</font>" if last else "", small),
        ])
    table = Table(rows, colWidths=[32 * mm, 32 * mm, 34 * mm, 22 * mm, 40 * mm, 20 * mm], repeatRows=1)
    table.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, 0), 8), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
    story += [Paragraph("Findings", st["Heading2"]), table, Spacer(1, 4 * mm),
              Paragraph("Each record is stored with the SHA-256 of its photo and chained to the previous "
                        "record of this unit (record hash). GET /maps/{id}/verify checks the chain.", small)]
    doc.build(story)
    return buf.getvalue()
