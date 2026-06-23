import base64
import io
import json
import re
import sqlite3
import zipfile
import traceback
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Request, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, Table, TableStyle

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
LOGS = BASE / "logs"
PDFS = BASE / "pdfs"
LOGO = BASE / "static" / "logo.png"
PROJECT_IMAGES = BASE / "project_images"
EXPORTS = BASE / "exports"
DB = DATA / "funkgeraete.db"
for p in (DATA, LOGS, PDFS, EXPORTS, PROJECT_IMAGES):
    p.mkdir(exist_ok=True)

VERSION = "1.0.2"
app = FastAPI(title="Funkgeräteverwaltung", version=VERSION)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

ACCESSORIES = [
    "Externe Sprech-/Hörkombi",
    "Gürtelholster",
    "Spare Batterie",
    "Single Ladeschale mit Netzteil",
    "Tarnset",
]


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fmt_datetime(value: str) -> str:
    if not value:
        return "-"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value[:19], fmt)
            if fmt == "%Y-%m-%d":
                return dt.strftime("%d.%m.%Y")
            return dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            pass
    return value


def fmt_date(value: str) -> str:
    if not value:
        return "-"
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except Exception:
        return value


templates.env.filters["dt"] = fmt_datetime
templates.env.filters["date_de"] = fmt_date


def error_page(request: Request, title: str, message: str, details: str = "", status_code: int = 500):
    error_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        with open(LOGS / "app_errors.txt", "a", encoding="utf-8") as f:
            f.write(f"\n===== FEHLER {error_id} =====\n")
            f.write(f"Pfad: {request.url.path}\n")
            f.write(f"Titel: {title}\n")
            f.write(f"Meldung: {message}\n")
            if details:
                f.write(details + "\n")
    except Exception:
        pass
    return templates.TemplateResponse(
        "error.html",
        {"request": request, "title": title, "message": message, "details": details, "error_id": error_id, "version": VERSION},
        status_code=status_code,
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return error_page(request, "Anfrage konnte nicht ausgeführt werden", str(exc.detail), "", exc.status_code)


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    details = traceback.format_exc()
    return error_page(
        request,
        "Unerwarteter Fehler",
        "Es ist ein Fehler aufgetreten. Kopiere bitte den Fehlerblock unten und sende ihn mir.",
        details,
        500,
    )


def safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9ÄÖÜäöüß _.-]", "", value).strip()
    return value or "Projekt"


def add_column_if_missing(con, table: str, column: str, definition: str):
    cols = [r["name"] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    with db() as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            pdf_logo_filename TEXT
        )
        """)
        add_column_if_missing(con, "projects", "pdf_logo_filename", "TEXT")
        con.execute("""
        CREATE TABLE IF NOT EXISTS loans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            date TEXT NOT NULL,
            radio_no TEXT NOT NULL,
            battery_no TEXT NOT NULL,
            accessories_json TEXT NOT NULL,
            custom_accessory TEXT,
            signature_data TEXT,
            return_json TEXT,
            return_note TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id)
        )
        """)
        add_column_if_missing(con, "loans", "sequence_no", "INTEGER")
        add_column_if_missing(con, "loans", "extra_accessories_json", "TEXT DEFAULT '[]'")
        add_column_if_missing(con, "loans", "closed_at", "TEXT")
        add_column_if_missing(con, "loans", "return_history_json", "TEXT DEFAULT '[]'")
        # Alte Daten nachträglich nummerieren.
        projects = con.execute("SELECT id FROM projects").fetchall()
        for p in projects:
            rows = con.execute("SELECT id FROM loans WHERE project_id=? ORDER BY created_at ASC, id ASC", (p["id"],)).fetchall()
            for idx, row in enumerate(rows, start=1):
                con.execute("UPDATE loans SET sequence_no=COALESCE(sequence_no, ?) WHERE id=?", (idx, row["id"]))


def now_de() -> str:
    return fmt_datetime(now())


def log(project_id: int, text: str):
    """Append a readable, separated event block to the project logfile."""
    path = LOGS / f"project_{project_id}.txt"
    with path.open("a", encoding="utf-8") as f:
        f.write("\n" + "=" * 64 + "\n")
        f.write(f"Zeitpunkt: {now_de()}\n")
        f.write("-" * 64 + "\n")
        f.write(text.strip() + "\n")



def get_project(project_id: int):
    with db() as con:
        row = con.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Projekt nicht gefunden")
    return row


def get_loan(loan_id: int):
    with db() as con:
        row = con.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Ausleihe nicht gefunden")
    return row


def format_accessories(accessories: List[str], extra_battery_no: str = "") -> List[str]:
    result = []
    for a in accessories:
        if a == "Spare Batterie" and extra_battery_no.strip():
            result.append(f"Spare Batterie Seriennummer {extra_battery_no.strip()}")
        else:
            result.append(a)
    return result


def loan_items(loan) -> List[str]:
    items = [f"Funkgerät Seriennummer {loan['radio_no']}", f"Batterie Seriennummer {loan['battery_no']}"]
    items += json.loads(loan["accessories_json"] or "[]")
    if loan["custom_accessory"]:
        items.append(loan["custom_accessory"])
    items += json.loads(loan["extra_accessories_json"] or "[]")
    return items




def normalize_serial(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip()).casefold()


def extract_serial_from_item(item: str) -> Optional[str]:
    prefixes = (
        "Funkgerät Seriennummer ",
        "Batterie Seriennummer ",
        "Spare Batterie Seriennummer ",
    )
    for prefix in prefixes:
        if item.startswith(prefix):
            return item[len(prefix):].strip()
    return None


def ensure_unique_serials(project_id: int, serials: List[tuple], exclude_loan_id: Optional[int] = None):
    """Prevent active duplicate serial numbers within a project, ignoring case and spaces."""
    cleaned = []
    seen_in_form = {}
    for label, serial in serials:
        serial = (serial or "").strip()
        if not serial:
            continue
        key = normalize_serial(serial)
        if key in seen_in_form:
            raise HTTPException(400, f"Seriennummer doppelt in dieser Eingabe: {serial} ({label}) ist identisch mit {seen_in_form[key]}.")
        seen_in_form[key] = label
        cleaned.append((label, serial, key))

    if not cleaned:
        return

    with db() as con:
        rows = con.execute("SELECT * FROM loans WHERE project_id=? AND status IN ('open','partial')", (project_id,)).fetchall()

    active_serials = {}
    for loan in rows:
        if exclude_loan_id is not None and loan["id"] == exclude_loan_id:
            # Do not skip completely: existing open items of the same loan still block duplicates.
            pass
        returned = set(json.loads(loan["return_json"] or "[]"))
        for item in loan_items(loan):
            if item in returned:
                continue
            serial = extract_serial_from_item(item)
            if serial:
                active_serials[normalize_serial(serial)] = f"{serial} bei Ausleihe #{loan['sequence_no']} / {loan['name']}"

    for label, serial, key in cleaned:
        if key in active_serials:
            raise HTTPException(400, f"Seriennummer bereits aktiv vergeben: {serial} ({label}). Bereits verwendet: {active_serials[key]}. Erst zurückgeben, dann erneut ausgeben.")

def project_title(proj, loan) -> str:
    return f"{proj['name']} - {loan['sequence_no']}"


def pdf_base_name(kind: str, proj, loan) -> str:
    # Dateiname ohne laufende Nummer am Ende, dafür mit Name der ausleihenden Person.
    # Beispiel: Ausleihe - Projektname - Max Müller.pdf
    return f"{kind} - {safe_filename(proj['name'])} - {safe_filename(loan['name'])}"


def project_pdf_logo_path(proj) -> Optional[Path]:
    fn = proj["pdf_logo_filename"] if "pdf_logo_filename" in proj.keys() else None
    if fn:
        path = PROJECT_IMAGES / fn
        if path.exists():
            return path
    return None


def styles():
    st = getSampleStyleSheet()
    st.add(ParagraphStyle(name="Small", parent=st["Normal"], fontSize=9, leading=12))
    st.add(ParagraphStyle(name="Line", parent=st["Normal"], fontSize=10, leading=14, wordWrap="CJK"))
    return st


def add_pdf_header(story, proj):
    # Hauptlogo immer links oben, optionales Projektlogo rechts oben.
    # Beide Logos werden gleich groß proportional eingepasst.
    left_logo = RLImage(str(LOGO), width=34*mm, height=22*mm, kind="proportional") if LOGO.exists() else ""
    second = project_pdf_logo_path(proj)
    right_logo = RLImage(str(second), width=34*mm, height=22*mm, kind="proportional") if second else ""
    if left_logo or right_logo:
        tbl = Table([[left_logo, right_logo]], colWidths=[85*mm, 85*mm], hAlign="CENTER")
        tbl.setStyle(TableStyle([
            ("ALIGN", (0,0), (0,0), "LEFT"),
            ("ALIGN", (1,0), (1,0), "RIGHT"),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("BOTTOMPADDING", (0,0), (-1,-1), 6),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 4*mm))


def add_signature(story, signature_data: str, st):
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph("<b>Unterschrift:</b>", st["Line"]))
    if not signature_data or not signature_data.startswith("data:image"):
        story.append(Paragraph("Keine digitale Unterschrift gespeichert.", st["Line"]))
        return
    try:
        b64 = signature_data.split(",", 1)[1]
        bio = io.BytesIO(base64.b64decode(b64))
        story.append(RLImage(bio, width=75*mm, height=22*mm, kind="proportional"))
    except Exception:
        story.append(Paragraph("Unterschrift konnte im PDF nicht eingebettet werden.", st["Line"]))


def para_lines(story, lines, st):
    for line in lines:
        if line == "":
            story.append(Spacer(1, 3*mm))
        elif line.endswith(":") and not line.startswith("-"):
            story.append(Paragraph(f"<b>{line}</b>", st["Line"]))
        else:
            story.append(Paragraph(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), st["Line"]))


def create_loan_pdf_file(loan_id: int):
    loan = get_loan(loan_id)
    proj = get_project(loan["project_id"])
    path = PDFS / f"{pdf_base_name('Ausleihe', proj, loan)}.pdf"
    st = styles()
    story = []
    add_pdf_header(story, proj)
    story.append(Paragraph(f"PDF erstellt am: {fmt_datetime(datetime.now().isoformat(timespec='seconds'))}", st["Small"]))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph("Funkgeräte-Ausgabe", st["Title"]))
    story.append(Spacer(1, 5*mm))
    base_items = [f"Funkgerät Seriennummer {loan['radio_no']}", f"Batterie Seriennummer {loan['battery_no']}"]
    accessories = json.loads(loan["accessories_json"] or "[]")
    custom = [loan["custom_accessory"]] if loan["custom_accessory"] else []
    extra = json.loads(loan["extra_accessories_json"] or "[]")
    lines = [
        f"Projekt: {proj['name']}",
        f"Ausleihe: {project_title(proj, loan)}",
        f"Name: {loan['name']}",
        f"Datum/Uhrzeit der Ausleihe: {fmt_datetime(loan['created_at'])}",
        "",
        "Ausgegeben:",
    ]
    for item in base_items + accessories + custom:
        lines.append(f"- {item}")
    if extra:
        lines += ["", "Nachträglich ausgegeben:"]
        for item in extra:
            lines.append(f"- {item}")
    para_lines(story, lines, st)
    add_signature(story, loan["signature_data"], st)
    doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=14*mm, bottomMargin=14*mm)
    doc.build(story)
    return path


def create_return_pdf_file(loan_id: int):
    loan = get_loan(loan_id)
    proj = get_project(loan["project_id"])
    returned = json.loads(loan["return_json"] or "[]")
    path = PDFS / f"{pdf_base_name('Rückgabe', proj, loan)}.pdf"
    st = styles()
    story = []
    add_pdf_header(story, proj)
    story.append(Paragraph(f"PDF erstellt am: {fmt_datetime(datetime.now().isoformat(timespec='seconds'))}", st["Small"]))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph("Funkgeräte-Rückgabe", st["Title"]))
    story.append(Spacer(1, 5*mm))
    lines = [
        f"Projekt: {proj['name']}",
        f"Ausleihe: {project_title(proj, loan)}",
        f"Name: {loan['name']}",
        f"Status: {'vollständig zurückgegeben' if loan['status'] == 'closed' else 'unvollständig zurückgegeben'}",
        f"Zeitpunkt vollständige Rückgabe: {fmt_datetime(loan['closed_at']) if loan['closed_at'] else 'noch nicht vollständig zurückgegeben'}",
        "",
        "Rückgabeintervalle:",
    ]
    history = json.loads(loan["return_history_json"] or "[]")
    if history:
        for ev in history:
            items = ", ".join(ev.get('items', [])) or 'keine Positionen'
            note = f" / Bemerkung: {ev.get('note')}" if ev.get('note') else ""
            lines.append(f"- {fmt_datetime(ev.get('timestamp', '-'))}: {items}{note}")
    else:
        lines.append("- noch keine Rückgabe erfasst")
    lines += ["", "Insgesamt zurückgegeben:"]
    for item in returned:
        lines.append(f"- {item}")
    missing = [i for i in loan_items(loan) if i not in returned]
    if missing:
        lines += ["", "Noch offen:"]
        for item in missing:
            lines.append(f"- {item}")
    if loan["return_note"]:
        lines += ["", f"Bemerkung: {loan['return_note']}"]
    para_lines(story, lines, st)
    doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=14*mm, bottomMargin=14*mm)
    doc.build(story)
    return path


@app.on_event("startup")
def startup():
    init_db()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    with db() as con:
        projects = con.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    return templates.TemplateResponse("index.html", {"request": request, "projects": projects})


@app.post("/projects")
def create_project(name: str = Form(...)):
    created = now()
    with db() as con:
        cur = con.execute("INSERT INTO projects(name, created_at) VALUES(?,?)", (name.strip(), created))
        pid = cur.lastrowid
    log(pid, f"AKTION: Projekt erstellt\nProjekt: {name.strip()}")
    return RedirectResponse(f"/projects/{pid}", status_code=303)


@app.post("/projects/import")
async def import_project(file: UploadFile = File(...)):
    content = await file.read()
    try:
        with zipfile.ZipFile(io.BytesIO(content), "r") as z:
            data = json.loads(z.read("project.json").decode("utf-8"))
            imported_log = z.read("log.txt").decode("utf-8") if "log.txt" in z.namelist() else ""
            imported_logo = z.read("project_pdf_logo") if "project_pdf_logo" in z.namelist() else None
    except Exception as exc:
        raise HTTPException(400, f"Import fehlgeschlagen: {exc}")
    with db() as con:
        cur = con.execute("INSERT INTO projects(name, created_at) VALUES(?,?)", (data["project"]["name"], now()))
        new_pid = cur.lastrowid
        logo_name = None
        if imported_logo:
            logo_name = f"project_{new_pid}_pdf_logo.png"
            (PROJECT_IMAGES / logo_name).write_bytes(imported_logo)
            con.execute("UPDATE projects SET pdf_logo_filename=? WHERE id=?", (logo_name, new_pid))
        for loan in data.get("loans", []):
            con.execute("""
            INSERT INTO loans(project_id,name,date,radio_no,battery_no,accessories_json,custom_accessory,signature_data,return_json,return_note,status,created_at,updated_at,sequence_no,extra_accessories_json,closed_at,return_history_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                new_pid, loan["name"], loan["date"], loan["radio_no"], loan["battery_no"],
                loan.get("accessories_json", "[]"), loan.get("custom_accessory", ""), loan.get("signature_data", ""),
                loan.get("return_json", "[]"), loan.get("return_note", ""), loan.get("status", "open"),
                loan.get("created_at", now()), loan.get("updated_at", now()), loan.get("sequence_no"),
                loan.get("extra_accessories_json", "[]"), loan.get("closed_at"), loan.get("return_history_json", "[]")
            ))
    (LOGS / f"project_{new_pid}.txt").write_text(imported_log, encoding="utf-8")
    log(new_pid, f"AKTION: Projekt importiert\nDatei: {file.filename}")
    return RedirectResponse(f"/projects/{new_pid}", status_code=303)


@app.get("/projects/{project_id}", response_class=HTMLResponse)
def project(request: Request, project_id: int):
    proj = get_project(project_id)
    with db() as con:
        loans = con.execute("SELECT * FROM loans WHERE project_id=? ORDER BY sequence_no DESC, created_at DESC", (project_id,)).fetchall()
    return templates.TemplateResponse("project.html", {"request": request, "project": proj, "loans": loans})


@app.get("/projects/{project_id}/new", response_class=HTMLResponse)
def new_loan(request: Request, project_id: int):
    proj = get_project(project_id)
    return templates.TemplateResponse("new_loan.html", {"request": request, "project": proj, "accessories": ACCESSORIES, "today": datetime.now().strftime("%Y-%m-%d")})


@app.post("/projects/{project_id}/loans")
def create_loan(project_id: int, name: str = Form(...), date: str = Form(...), radio_no: str = Form(...), battery_no: str = Form(...), accessories: List[str] = Form([]), extra_battery_no: str = Form(""), custom_accessory: str = Form(""), signature_data: str = Form("")):
    get_project(project_id)
    if not radio_no.strip() or not battery_no.strip():
        raise HTTPException(400, "Funkgerät- und Batterie-Seriennummer müssen angegeben werden.")
    if "Spare Batterie" in accessories and not extra_battery_no.strip():
        raise HTTPException(400, "Bei Spare Batterie muss eine Spare Batterie Seriennummer angegeben werden.")
    serials_to_check = [("Funkgerät Seriennummer", radio_no), ("Batterie Seriennummer", battery_no)]
    if "Spare Batterie" in accessories:
        serials_to_check.append(("Spare Batterie Seriennummer", extra_battery_no))
    ensure_unique_serials(project_id, serials_to_check)
    t = now()
    accessory_list = format_accessories(accessories, extra_battery_no)
    with db() as con:
        next_seq = (con.execute("SELECT COALESCE(MAX(sequence_no), 0) + 1 AS n FROM loans WHERE project_id=?", (project_id,)).fetchone()["n"])
        cur = con.execute("""
            INSERT INTO loans(project_id,name,date,radio_no,battery_no,accessories_json,custom_accessory,signature_data,return_json,status,created_at,updated_at,sequence_no,extra_accessories_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (project_id, name.strip(), date, radio_no.strip(), battery_no.strip(), json.dumps(accessory_list, ensure_ascii=False), custom_accessory.strip(), signature_data, json.dumps([], ensure_ascii=False), "open", t, t, next_seq, json.dumps([], ensure_ascii=False)))
        lid = cur.lastrowid
    log(project_id, f"AKTION: Ausgabe angelegt\nAusleihe: #{next_seq}\nName: {name}\nDatum: {fmt_date(date)}\nFunkgerät Seriennummer: {radio_no}\nBatterie Seriennummer: {battery_no}\nZubehör: {', '.join(accessory_list + ([custom_accessory] if custom_accessory else [])) or '-'}")
    return RedirectResponse(f"/loans/{lid}", status_code=303)


@app.get("/loans/{loan_id}", response_class=HTMLResponse)
def loan_detail(request: Request, loan_id: int):
    loan = get_loan(loan_id)
    proj = get_project(loan["project_id"])
    returned = json.loads(loan["return_json"] or "[]")
    missing = [i for i in loan_items(loan) if i not in returned]
    return templates.TemplateResponse("loan.html", {"request": request, "project": proj, "loan": loan, "items": loan_items(loan), "returned": returned, "missing": missing, "accessories": ACCESSORIES, "return_history": json.loads(loan["return_history_json"] or "[]")})


@app.post("/loans/{loan_id}/add-items")
def add_items(loan_id: int, accessories: List[str] = Form([]), extra_battery_no: str = Form(""), custom_accessory: str = Form("")):
    loan = get_loan(loan_id)
    if "Spare Batterie" in accessories and not extra_battery_no.strip():
        raise HTTPException(400, "Bei Spare Batterie muss eine Spare Batterie Seriennummer angegeben werden.")
    if "Spare Batterie" in accessories:
        ensure_unique_serials(loan["project_id"], [("Spare Batterie Seriennummer", extra_battery_no)], exclude_loan_id=loan_id)
    new_items = format_accessories(accessories, extra_battery_no)
    if custom_accessory.strip():
        new_items.append(custom_accessory.strip())
    if not new_items:
        return RedirectResponse(f"/loans/{loan_id}", status_code=303)
    old = json.loads(loan["extra_accessories_json"] or "[]")
    combined = old + new_items
    status = "partial" if json.loads(loan["return_json"] or "[]") else "open"
    with db() as con:
        con.execute("UPDATE loans SET extra_accessories_json=?, status=?, updated_at=? WHERE id=?", (json.dumps(combined, ensure_ascii=False), status, now(), loan_id))
    log(loan["project_id"], f"AKTION: Nachträgliche Ausgabe\nAusleihe: #{loan['sequence_no']}\nName: {loan['name']}\nNeu ausgegeben: {', '.join(new_items)}")
    return RedirectResponse(f"/loans/{loan_id}", status_code=303)


@app.get("/loans/{loan_id}/return", response_class=HTMLResponse)
def return_form(request: Request, loan_id: int):
    loan = get_loan(loan_id)
    proj = get_project(loan["project_id"])
    returned = json.loads(loan["return_json"] or "[]")
    return templates.TemplateResponse("return.html", {"request": request, "project": proj, "loan": loan, "items": loan_items(loan), "returned": returned})


@app.post("/loans/{loan_id}/return")
def save_return(loan_id: int, returned_items: List[str] = Form([]), return_note: str = Form("")):
    loan = get_loan(loan_id)
    all_items = loan_items(loan)
    previous = json.loads(loan["return_json"] or "[]")
    newly_returned = [i for i in returned_items if i not in previous]
    complete = set(returned_items) == set(all_items)
    status = "closed" if complete else ("partial" if returned_items else "open")
    timestamp = now()
    closed_at = timestamp if complete else None
    history = json.loads(loan["return_history_json"] or "[]")
    if newly_returned or return_note.strip():
        history.append({"timestamp": timestamp, "items": newly_returned, "all_returned_after_save": returned_items, "note": return_note.strip()})
    with db() as con:
        con.execute("UPDATE loans SET return_json=?, return_note=?, status=?, closed_at=?, updated_at=?, return_history_json=? WHERE id=?", (json.dumps(returned_items, ensure_ascii=False), return_note.strip(), status, closed_at, timestamp, json.dumps(history, ensure_ascii=False), loan_id))
    log(loan["project_id"], f"AKTION: Rückgabe gespeichert\nAusleihe: #{loan['sequence_no']}\nName: {loan['name']}\nZeitpunkt: {fmt_datetime(timestamp)}\nNeu zurück: {', '.join(newly_returned) or '-'}\nZurück insgesamt: {', '.join(returned_items) or '-'}\nStatus: {'abgeschlossen' if status=='closed' else ('unvollständig' if status=='partial' else 'offen')}\nZeitpunkt vollständige Rückgabe: {fmt_datetime(closed_at) if closed_at else '-'}\nBemerkung: {return_note or '-'}")
    return RedirectResponse(f"/loans/{loan_id}", status_code=303)


@app.post("/projects/{project_id}/pdf-logo")
async def upload_project_pdf_logo(project_id: int, file: UploadFile = File(...)):
    proj = get_project(project_id)
    suffix = Path(file.filename or "logo.png").suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg"):
        raise HTTPException(400, "Bitte PNG oder JPG hochladen.")
    filename = f"project_{project_id}_pdf_logo{suffix}"
    content = await file.read()
    (PROJECT_IMAGES / filename).write_bytes(content)
    with db() as con:
        con.execute("UPDATE projects SET pdf_logo_filename=? WHERE id=?", (filename, project_id))
    log(project_id, f"AKTION: Zusätzliches PDF-Logo hochgeladen\nDatei: {file.filename}")
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@app.get("/projects/{project_id}/delete", response_class=HTMLResponse)
def delete_confirm(request: Request, project_id: int):
    proj = get_project(project_id)
    return templates.TemplateResponse("delete_project.html", {"request": request, "project": proj})


@app.get("/projects/{project_id}/log")
def download_log(project_id: int):
    proj = get_project(project_id)
    path = LOGS / f"project_{project_id}.txt"
    if not path.exists():
        path.write_text("", encoding="utf-8")
    return FileResponse(path, filename=f"Log - {safe_filename(proj['name'])}.txt", media_type="text/plain")


@app.get("/projects/{project_id}/export")
def export_project(project_id: int):
    proj = get_project(project_id)
    with db() as con:
        loans = con.execute("SELECT * FROM loans WHERE project_id=? ORDER BY sequence_no ASC", (project_id,)).fetchall()
    data = {"version": VERSION, "project": dict(proj), "loans": [dict(l) for l in loans]}
    export_path = EXPORTS / f"Projekt - {safe_filename(proj['name'])}.zip"
    log_path = LOGS / f"project_{project_id}.txt"
    with zipfile.ZipFile(export_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json", json.dumps(data, ensure_ascii=False, indent=2))
        z.writestr("log.txt", log_path.read_text(encoding="utf-8") if log_path.exists() else "")
        second_logo = project_pdf_logo_path(proj)
        if second_logo:
            z.write(second_logo, "project_pdf_logo")
        for loan in loans:
            for fn in (create_loan_pdf_file(loan["id"]), create_return_pdf_file(loan["id"])):
                if fn.exists():
                    z.write(fn, f"pdfs/{fn.name}")
    log(project_id, "AKTION: Projekt exportiert")
    return FileResponse(export_path, filename=export_path.name, media_type="application/zip")


@app.post("/projects/{project_id}/delete")
def delete_project(project_id: int):
    proj = get_project(project_id)
    with db() as con:
        con.execute("DELETE FROM loans WHERE project_id=?", (project_id,))
        con.execute("DELETE FROM projects WHERE id=?", (project_id,))
    log_path = LOGS / f"project_{project_id}.txt"
    if log_path.exists():
        deleted_log = LOGS / f"deleted_project_{project_id}_{safe_filename(proj['name'])}.txt"
        log_path.rename(deleted_log)
    return RedirectResponse("/", status_code=303)


@app.get("/loans/{loan_id}/pdf")
def loan_pdf(loan_id: int):
    path = create_loan_pdf_file(loan_id)
    loan = get_loan(loan_id)
    log(loan["project_id"], f"AKTION: Ausleihe-PDF erzeugt\nDatei: {path.name}")
    return FileResponse(path, filename=path.name, media_type="application/pdf")


@app.get("/loans/{loan_id}/return-pdf")
def return_pdf(loan_id: int):
    path = create_return_pdf_file(loan_id)
    loan = get_loan(loan_id)
    log(loan["project_id"], f"AKTION: Rückgabe-PDF erzeugt\nDatei: {path.name}")
    return FileResponse(path, filename=path.name, media_type="application/pdf")
