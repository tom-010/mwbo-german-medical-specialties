"""Parse MWBO HTML files and produce schema-valid JSON (new version)."""

import json
import re
import sys
from pathlib import Path
from bs4 import BeautifulSoup, Tag


def norm(text: str) -> str:
    """Collapse whitespace and strip."""
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()


def cell_text(td) -> str:
    if td is None:
        return ""
    return norm(td.get_text(" "))


def is_section_heading(tds) -> tuple[bool, str]:
    """Return (True, heading_text) if this row is a section heading, else (False, '')."""
    if not tds:
        return False, ""

    # Single td with colspan=3 containing bold text
    if len(tds) == 1:
        td = tds[0]
        if str(td.get("colspan", "")) == "3":
            bold = td.find("strong")
            if bold:
                txt = norm(bold.get_text(" "))
                if txt:
                    return True, txt
        return False, ""

    # Multiple tds: col1 bold, col2+col3 empty
    if len(tds) >= 3:
        col1, col2 = tds[0], tds[1]
        col3 = tds[2] if len(tds) > 2 else None
        col1_bold = col1.find("strong")
        col1_txt = cell_text(col1)
        col2_txt = cell_text(col2)
        col3_txt = cell_text(col3) if col3 else ""
        if col1_bold and col1_txt and not col2_txt and not col3_txt:
            return True, col1_txt

    return False, ""


def parse_richtzahl(raw: str):
    if not raw:
        return None
    cleaned = re.sub(r'[.,\s]', '', raw.strip())
    try:
        return int(cleaned)
    except ValueError:
        return None


def parse_content_tables(tables) -> list:
    """Parse all 3-column Weiterbildungsinhalte tables."""
    results = []
    current_section = None

    for table in tables:
        for tr in table.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            if not tds:
                continue

            # Skip pure header rows
            if all(td.name == "th" for td in tds):
                continue

            # Skip completely empty rows
            if not any(cell_text(td) for td in tds):
                continue

            # Check if section heading
            is_heading, heading_txt = is_section_heading(tds)
            if is_heading:
                current_section = heading_txt
                continue

            # Content row - need at least 3 cols or handle gracefully
            col1 = cell_text(tds[0]) if len(tds) > 0 else ""
            col2 = cell_text(tds[1]) if len(tds) > 1 else ""
            col3_raw = cell_text(tds[2]) if len(tds) > 2 else ""

            if not col1 and not col2:
                continue

            if current_section is None:
                continue

            item = {"abschnitt": current_section}
            if col1:
                item["kognitive_und_methodenkompetenz"] = col1
            if col2:
                item["handlungskompetenz"] = col2
            rz = parse_richtzahl(col3_raw)
            if rz is not None:
                item["richtzahl"] = rz

            results.append(item)

    return results


def parse_meta_table(table) -> dict:
    """Parse the 2-column metadata table."""
    data = {}
    for tr in table.find_all("tr"):
        tds = tr.find_all(["td", "th"])

        # Single wide cell (schwerpunkt Voraussetzung)
        if len(tds) == 1:
            td = tds[0]
            if str(td.get("colspan", "")) == "2":
                txt = cell_text(td)
                if txt:
                    data["_voraussetzung_raw"] = txt
            continue

        if len(tds) < 2:
            continue

        key = cell_text(tds[0])
        val = cell_text(tds[1])

        if "Gebietsdefinition" in key:
            data["gebietsdefinition"] = val
        elif "Weiterbildungszeit" in key:
            data["weiterbildungszeit"] = val
        elif "Voraussetzung" in key:
            data["voraussetzung"] = val
        elif "Definition" in key and "Mindest" not in key:
            data["definition"] = val
        elif "Mindestanforderungen" in key:
            data["mindestanforderungen"] = val

    return data


def is_content_table(table) -> bool:
    """Check if a table is a 3-column Weiterbildungsinhalte table."""
    ths = table.find_all("th")
    th_texts = [cell_text(th) for th in ths]
    return any("Richtzahl" in t or "Handlungskompetenz" in t for t in th_texts)


def parse_html(html_path: Path) -> dict:
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")

    h3 = soup.find("h3")
    h4 = soup.find("h4")
    h3_text = norm(h3.get_text(" ")) if h3 else ""
    h4_text = norm(h4.get_text(" ")) if h4 else ""

    # Determine typ
    if h3_text.startswith("Zusatz-Weiterbildung"):
        typ = "zusatz-weiterbildung"
        gebiet = re.sub(r'^Zusatz-Weiterbildung\s+', '', h3_text).strip()
    elif "Schwerpunkt" in h4_text:
        typ = "schwerpunkt"
        gebiet = re.sub(r'^Gebiet\s+', '', h3_text).strip()
    else:
        typ = "facharzt"
        gebiet = re.sub(r'^Gebiet\s+', '', h3_text).strip()

    bezeichnung = h4_text

    # zusatzbezeichnung: blockquote immediately after h4
    zusatzbezeichnung = None
    if h4:
        sib = h4.find_next_sibling()
        if sib and sib.name == "blockquote":
            bq_text = norm(sib.get_text(" "))
            m = re.match(r'^\((.+)\)\s*$', bq_text)
            if m:
                zusatzbezeichnung = m.group(1).strip()

    # Classify tables
    all_tables = soup.find_all("table")
    meta_data = {}
    content_tables = []

    for i, tbl in enumerate(all_tables):
        if is_content_table(tbl):
            content_tables.append(tbl)
        else:
            if not content_tables:  # Only parse meta from pre-content tables
                md = parse_meta_table(tbl)
                meta_data.update(md)

    # voraussetzung for schwerpunkt
    voraussetzung = meta_data.get("voraussetzung")
    if not voraussetzung and "_voraussetzung_raw" in meta_data:
        voraussetzung = meta_data["_voraussetzung_raw"]

    # Parse content
    inhalte = parse_content_tables(content_tables)

    # Build result (omit null fields)
    result = {
        "typ": typ,
        "gebiet": gebiet,
        "bezeichnung": bezeichnung,
    }

    if zusatzbezeichnung:
        result["zusatzbezeichnung"] = zusatzbezeichnung

    if typ == "facharzt" and meta_data.get("gebietsdefinition"):
        result["gebietsdefinition"] = meta_data["gebietsdefinition"]
    elif typ == "schwerpunkt" and voraussetzung:
        result["voraussetzung"] = voraussetzung
    elif typ == "zusatz-weiterbildung":
        if meta_data.get("definition"):
            result["definition"] = meta_data["definition"]
        if meta_data.get("mindestanforderungen"):
            result["mindestanforderungen"] = meta_data["mindestanforderungen"]

    if meta_data.get("weiterbildungszeit"):
        result["weiterbildungszeit"] = meta_data["weiterbildungszeit"]

    result["inhalte"] = inhalte

    return result


FILES = [
    "gebiet-allgemeinmedizin.html",
    "gebiet-anaesthesiologie.html",
    "gebiet-anatomie.html",
    "gebiet-arbeitsmedizin.html",
    "gebiet-augenheilkunde.html",
    "gebiet-biochemie.html",
    "gebiet-chirurgie-facharzt-orthopaedie-und-unfallchirurgie-schwerpunkt-orthopaedische-rheumatologie.html",
]

if __name__ == "__main__":
    base = Path("/home/tom/Projects/health_bot/gebiete")
    for fname in FILES:
        html_path = base / fname
        json_path = html_path.with_suffix(".json")
        print(f"Parsing {fname}...", end=" ", flush=True)
        try:
            data = parse_html(html_path)
            json_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            print(f"OK ({len(data['inhalte'])} inhalte)")
        except Exception as e:
            print(f"FAILED: {e}", file=sys.stderr)
            import traceback; traceback.print_exc()
