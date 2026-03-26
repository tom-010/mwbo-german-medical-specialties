#!/usr/bin/env python3
"""
Pipeline: mwbo.docx -> catalog.duckdb

Splits the docx into per-section HTML files, parses each into JSON,
merges into a validated catalog, and loads into a normalized DuckDB database.

Usage:
    .venv/bin/python pipeline.py run
    .venv/bin/python pipeline.py run --input other.docx --output out.duckdb
    .venv/bin/python pipeline.py split
    .venv/bin/python pipeline.py parse
    .venv/bin/python pipeline.py build
    .venv/bin/python pipeline.py compare catalog.duckdb catalog2.duckdb
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Literal, Optional, Union

import click
import duckdb
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field
from slugify import slugify as make_slug

log = logging.getLogger("mwbo")

BASE_DIR = Path(__file__).parent
TMP_DIR = BASE_DIR / "tmp_data"

TABLES = [
    "medical_fields",
    "specialties",
    "sub_specialties",
    "additional_qualifications",
    "competency_sections",
    "competency_items",
]


# ============================================================================
# Pydantic models
# ============================================================================


class KnowledgeItem(BaseModel):
    type: Literal["knowledge"] = "knowledge"
    description: str
    target_number: Optional[int] = None
    sub_items: Optional[list[CompetencyItem]] = None


class SkillItem(BaseModel):
    type: Literal["skill"] = "skill"
    description: str
    target_number: Optional[int] = None
    sub_items: Optional[list[CompetencyItem]] = None


class CourseItem(BaseModel):
    type: Literal["course"] = "course"
    description: str


CompetencyItem = Annotated[
    Union[KnowledgeItem, SkillItem, CourseItem],
    Field(discriminator="type"),
]

KnowledgeItem.model_rebuild()
SkillItem.model_rebuild()


class CompetencySection(BaseModel):
    name: str
    items: list[CompetencyItem]


class MedicalField(BaseModel):
    id: str
    name: str


class Specialty(BaseModel):
    name: str
    medical_field_id: str
    alternative_title: Optional[str] = None
    field_definition: str
    training_duration: str
    content: list[CompetencySection]


class SubSpecialty(BaseModel):
    name: str
    parent_specialty: str
    alternative_title: Optional[str] = None
    prerequisite: str
    training_duration: str
    content: list[CompetencySection]


class AdditionalQualification(BaseModel):
    name: str
    definition: str
    requirements: str
    content: list[CompetencySection]


class Catalog(BaseModel):
    medical_fields: list[MedicalField]
    specialties: list[Specialty]
    sub_specialties: list[SubSpecialty]
    additional_qualifications: list[AdditionalQualification]


# ============================================================================
# Step 1: Split docx into per-section HTML files
# ============================================================================

SECTION_PREFIXES = ("Gebiet", "Zusatz-Weiterbildung")


def slugify_filename(text: str) -> str:
    """Filesystem-safe slug with German umlaut expansion (ae/oe/ue)."""
    text = text.lower().strip()
    for old, new in [("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")]:
        text = text.replace(old, new)
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def split_docx(docx_path: Path, output_dir: Path) -> list[Path]:
    """Convert docx -> HTML via pandoc, split at H3 section boundaries."""
    log.info("Converting %s to HTML via pandoc", docx_path.name)

    result = subprocess.run(
        ["pandoc", str(docx_path), "-t", "html", "--standalone"],
        capture_output=True,
        text=True,
        check=True,
    )

    soup = BeautifulSoup(result.stdout, "html.parser")
    body = soup.find("body")
    if not body:
        raise click.ClickException("No <body> in pandoc output")

    sections: list[dict] = []
    current: dict | None = None

    for child in body.children:
        if not isinstance(child, Tag):
            continue

        if child.name == "h3":
            text = child.get_text(strip=True)
            if any(text.startswith(p) for p in SECTION_PREFIXES):
                if current:
                    sections.append(current)
                current = {"h3": text, "h4": None, "elements": [child]}
                continue

        if child.name == "h2" and current:
            sections.append(current)
            current = None
            continue

        if current:
            if child.name == "h4" and current["h4"] is None:
                current["h4"] = child.get_text(strip=True)
            if current["h4"] is None and child.name == "blockquote":
                strong = child.find("strong")
                if strong:
                    current["h4"] = strong.get_text(strip=True)
            current["elements"].append(child)

    if current:
        sections.append(current)

    # Unique filenames — disambiguate duplicate H3s using H4 subtitle
    name_counts: dict[str, int] = {}
    for s in sections:
        name_counts[s["h3"]] = name_counts.get(s["h3"], 0) + 1

    filenames: list[str] = []
    for s in sections:
        if name_counts[s["h3"]] > 1 and s["h4"]:
            h4 = s["h4"]
            m = re.search(
                r"(?:Facharzt/Fachärztin für|Facharzt(?:arzt)?/Fachärztin)\s+(.+)",
                h4,
            )
            subtitle = m.group(1) if m else h4
            filenames.append(slugify_filename(f"{s['h3']} - {subtitle}"))
        else:
            filenames.append(slugify_filename(s["h3"]))

    # Counter suffix for any remaining dupes
    seen: dict[str, int] = {}
    unique: list[str] = []
    for fn in filenames:
        if fn in seen:
            seen[fn] += 1
            unique.append(f"{fn}-{seen[fn]}")
        else:
            seen[fn] = 0
            unique.append(fn)

    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for s, fn in zip(sections, unique):
        body_html = "\n".join(str(el) for el in s["elements"])
        html = (
            '<!DOCTYPE html>\n<html lang="de">\n'
            f'<head><meta charset="utf-8"><title>{s["h3"]}</title></head>\n'
            f"<body>\n{body_html}\n</body>\n</html>\n"
        )
        p = output_dir / f"{fn}.html"
        p.write_text(html, encoding="utf-8")
        paths.append(p)
        log.debug("  %s", p.name)

    log.info("Split into %d HTML files -> %s", len(paths), output_dir)
    return paths


# ============================================================================
# Step 2: Parse HTML -> per-file JSON
# ============================================================================


def norm(text: str) -> str:
    """Collapse whitespace and strip."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def cell_text(td) -> str:
    if td is None:
        return ""
    return norm(td.get_text(" "))


def parse_richtzahl(text: str) -> int | None:
    if not text:
        return None
    cleaned = re.sub(r"[.,\s]", "", text.strip())
    try:
        return int(cleaned)
    except ValueError:
        return None


def parse_meta_table(table) -> dict:
    """Extract metadata (definition, Weiterbildungszeit, etc.) from a 2-col table."""
    data: dict[str, str] = {}

    for tr in table.find_all("tr"):
        tds = tr.find_all(["td", "th"])

        # Single wide cell — schwerpunkt Voraussetzung (colspan=2 in thead)
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
    """Identify 3-col competency tables and 1-col Kurs tables."""
    ths = table.find_all("th")
    th_texts = [cell_text(th) for th in ths]

    if any("Richtzahl" in t or "Handlungskompetenz" in t for t in th_texts):
        return True

    # 1-col Kurs tables
    cols = table.find_all("col")
    if len(cols) == 1:
        style = cols[0].get("style", "")
        if "100%" in style:
            return True
    if any("Kursinhalte" in t or "Kurs-Weiterbildung" in t for t in th_texts):
        return True

    return False


def parse_content_tables(tables: list) -> list[dict]:
    """Parse 3-col and 1-col content tables into flat inhalte list."""
    inhalte: list[dict] = []
    current_section: str | None = None

    for table in tables:
        cols = table.find_all("col")
        num_cols = len(cols) if cols else None

        for tr in table.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            if not tds:
                continue

            # Pure header row (all th) — capture section title from Kurs tables
            if all(td.name == "th" for td in tds):
                if len(tds) == 1:
                    txt = cell_text(tds[0])
                    if txt and "Kognitive" not in txt:
                        current_section = txt
                continue

            # Skip empty rows
            if not any(cell_text(td) for td in tds):
                continue

            first = tds[0]
            colspan = first.get("colspan")

            # Colspan section heading
            if colspan and int(colspan) >= 2:
                txt = cell_text(first)
                if txt:
                    current_section = txt
                continue

            # 3-column data row
            if len(tds) >= 3 or (num_cols is not None and num_cols >= 3):
                col1 = cell_text(tds[0]) if len(tds) > 0 else ""
                col2 = cell_text(tds[1]) if len(tds) > 1 else ""
                col3 = cell_text(tds[2]) if len(tds) > 2 else ""

                if not col1 and not col2:
                    continue

                entry: dict = {"abschnitt": current_section or ""}
                if col1:
                    entry["kognitive_und_methodenkompetenz"] = col1
                if col2:
                    entry["handlungskompetenz"] = col2
                if col3:
                    rz = parse_richtzahl(col3)
                    if rz is not None:
                        entry["richtzahl"] = rz
                inhalte.append(entry)

            elif len(tds) == 1:
                # 1-column Kurs content row
                txt = cell_text(tds[0])
                if not txt:
                    continue
                if tds[0].find("strong"):
                    current_section = txt
                else:
                    inhalte.append({"abschnitt": current_section or "", "text": txt})

    return inhalte


def parse_html_file(filepath: Path) -> dict:
    """Parse one HTML file into the intermediate JSON schema."""
    soup = BeautifulSoup(filepath.read_text(encoding="utf-8"), "html.parser")

    h3 = soup.find("h3")
    h4 = soup.find("h4")
    h3_text = norm(h3.get_text(" ")) if h3 else ""
    h4_text = norm(h4.get_text(" ")) if h4 else ""

    # Fallback: schwerpunkt designation in <blockquote><strong> after h3
    if not h4_text and h3:
        for sib in h3.find_next_siblings():
            if sib.name == "blockquote":
                strong = sib.find("strong")
                if strong:
                    h4_text = norm(strong.get_text(" "))
                    break
            elif sib.name in ("h3", "h4", "table"):
                break

    # Determine type
    if h3_text.startswith("Zusatz-Weiterbildung"):
        typ = "zusatz-weiterbildung"
        gebiet = re.sub(r"^Zusatz-Weiterbildung\s+", "", h3_text).strip()
        bezeichnung = h3_text
    elif "Schwerpunkt" in h4_text:
        typ = "schwerpunkt"
        gebiet = re.sub(r"^Gebiet\s+", "", h3_text).strip()
        # Strip sub-designation after en-dash (e.g. "Chirurgie – Facharzt X" -> "Chirurgie")
        gebiet = re.split(r"\s*[–—]\s*", gebiet)[0].strip()
        bezeichnung = h4_text
    else:
        typ = "facharzt"
        gebiet = re.sub(r"^Gebiet\s+", "", h3_text).strip()
        # Strip sub-designation after en-dash
        gebiet = re.split(r"\s*[–—]\s*", gebiet)[0].strip()
        bezeichnung = h4_text

    # zusatzbezeichnung: "(Hausarzt/Hausärztin)" in blockquote after h4
    zusatzbezeichnung = None
    anchor = h4 if h4 else None
    if anchor:
        sib = anchor.find_next_sibling()
        if sib and sib.name == "blockquote":
            bq_text = norm(sib.get_text(" "))
            m = re.match(r"^\((.+)\)$", bq_text)
            if m:
                zusatzbezeichnung = m.group(1).strip()

    # Classify tables
    all_tables = soup.find_all("table")
    meta_data: dict = {}
    content_tables: list = []

    for tbl in all_tables:
        if is_content_table(tbl):
            content_tables.append(tbl)
        elif not content_tables:
            md = parse_meta_table(tbl)
            meta_data.update(md)

    # Fallback: if is_content_table matched nothing, treat all tables after
    # the first as content
    if not content_tables and len(all_tables) > 1:
        meta_data = parse_meta_table(all_tables[0])
        content_tables = all_tables[1:]

    voraussetzung = meta_data.get("voraussetzung")
    if not voraussetzung and "_voraussetzung_raw" in meta_data:
        voraussetzung = meta_data["_voraussetzung_raw"]

    inhalte = parse_content_tables(content_tables)

    log.debug(
        "  %s: typ=%s gebiet=%s inhalte=%d",
        filepath.name,
        typ,
        gebiet,
        len(inhalte),
    )

    result: dict = {"typ": typ, "gebiet": gebiet, "bezeichnung": bezeichnung}
    if zusatzbezeichnung:
        result["zusatzbezeichnung"] = zusatzbezeichnung

    if typ == "facharzt":
        if meta_data.get("gebietsdefinition"):
            result["gebietsdefinition"] = meta_data["gebietsdefinition"]
        if meta_data.get("weiterbildungszeit"):
            result["weiterbildungszeit"] = meta_data["weiterbildungszeit"]
    elif typ == "schwerpunkt":
        if voraussetzung:
            result["voraussetzung"] = voraussetzung
        if meta_data.get("weiterbildungszeit"):
            result["weiterbildungszeit"] = meta_data["weiterbildungszeit"]
    elif typ == "zusatz-weiterbildung":
        if meta_data.get("definition"):
            result["definition"] = meta_data["definition"]
        if meta_data.get("mindestanforderungen"):
            result["mindestanforderungen"] = meta_data["mindestanforderungen"]

    result["inhalte"] = inhalte
    return result


def parse_all_html(html_dir: Path, json_dir: Path) -> list[Path]:
    """Parse every HTML file into a JSON file."""
    json_dir.mkdir(parents=True, exist_ok=True)

    html_files = sorted(html_dir.glob("*.html"))
    log.info("Parsing %d HTML files -> %s", len(html_files), json_dir)
    paths: list[Path] = []
    errors: list[tuple[str, str]] = []

    for hp in html_files:
        try:
            data = parse_html_file(hp)
            jp = json_dir / hp.with_suffix(".json").name
            jp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            paths.append(jp)
        except Exception as e:
            log.error("Failed to parse %s: %s", hp.name, e)
            errors.append((hp.name, str(e)))

    log.info("Parsed %d files, %d errors", len(paths), len(errors))
    return paths


# ============================================================================
# Step 3: Merge per-file JSONs -> Catalog
# ============================================================================


def parse_competency_items(
    raw: dict,
) -> list[KnowledgeItem | SkillItem | CourseItem]:
    """Convert one raw inhalte entry into typed competency items."""
    sub_items: list[KnowledgeItem | SkillItem | CourseItem] | None = None
    if "inhalte" in raw:
        sub_items = []
        for sub in raw["inhalte"]:
            sub_items.extend(parse_competency_items(sub))

    items: list[KnowledgeItem | SkillItem | CourseItem] = []
    knowledge = raw.get("kognitive_und_methodenkompetenz")
    skills = raw.get("handlungskompetenz")
    target_number = raw.get("richtzahl")
    text = raw.get("text")

    if knowledge:
        items.append(
            KnowledgeItem(
                description=knowledge,
                target_number=target_number if not skills else None,
                sub_items=sub_items if not skills else None,
            )
        )
    if skills:
        items.append(
            SkillItem(
                description=skills,
                target_number=target_number,
                sub_items=sub_items,
            )
        )
    if text:
        items.append(CourseItem(description=text))

    return items


def group_content(raw_items: list[dict]) -> list[CompetencySection]:
    """Group flat inhalte list into CompetencySection objects by abschnitt."""
    sections: list[CompetencySection] = []
    current_name: str | None = None
    current_items: list[KnowledgeItem | SkillItem | CourseItem] = []

    for raw in raw_items:
        abschnitt = raw.get("abschnitt", "")
        if abschnitt != current_name:
            if current_name is not None and current_items:
                sections.append(
                    CompetencySection(name=current_name, items=current_items)
                )
            current_name = abschnitt
            current_items = []
        current_items.extend(parse_competency_items(raw))

    if current_name is not None and current_items:
        sections.append(CompetencySection(name=current_name, items=current_items))

    return sections


def extract_parent_specialty(voraussetzung: str) -> str:
    m = re.search(r"Facharzt-Weiterbildung\s+(.+?)\s+auf", voraussetzung)
    if m:
        return m.group(1).rstrip(".")
    return voraussetzung


def build_catalog(json_dir: Path) -> Catalog:
    """Read per-specialty JSON files and assemble a Catalog."""
    json_files = sorted(json_dir.glob("*.json"))
    log.info("Building catalog from %d JSON files in %s", len(json_files), json_dir)

    medical_fields: dict[str, MedicalField] = {}
    specialties: list[Specialty] = []
    sub_specialties: list[SubSpecialty] = []
    additional_qualifications: list[AdditionalQualification] = []

    for path in json_files:
        data = json.loads(path.read_text(encoding="utf-8"))
        typ = data["typ"]
        gebiet = data["gebiet"]
        field_id = make_slug(gebiet)

        if typ in ("facharzt", "schwerpunkt") and field_id not in medical_fields:
            medical_fields[field_id] = MedicalField(id=field_id, name=gebiet)

        content = group_content(data.get("inhalte", []))

        try:
            if typ == "facharzt":
                if not data.get("gebietsdefinition"):
                    log.warning("%s: missing gebietsdefinition", path.name)
                if not data.get("weiterbildungszeit"):
                    log.warning("%s: missing weiterbildungszeit", path.name)
                specialties.append(
                    Specialty(
                        name=data["bezeichnung"],
                        medical_field_id=field_id,
                        alternative_title=data.get("zusatzbezeichnung"),
                        field_definition=data.get("gebietsdefinition", ""),
                        training_duration=data.get("weiterbildungszeit", ""),
                        content=content,
                    )
                )
            elif typ == "schwerpunkt":
                if not data.get("voraussetzung"):
                    log.warning("%s: missing voraussetzung", path.name)
                sub_specialties.append(
                    SubSpecialty(
                        name=data["bezeichnung"],
                        parent_specialty=extract_parent_specialty(
                            data.get("voraussetzung", "")
                        ),
                        alternative_title=data.get("zusatzbezeichnung"),
                        prerequisite=data.get("voraussetzung", ""),
                        training_duration=data.get("weiterbildungszeit", ""),
                        content=content,
                    )
                )
            elif typ == "zusatz-weiterbildung":
                if not data.get("definition"):
                    log.warning("%s: missing definition", path.name)
                if not data.get("mindestanforderungen"):
                    log.warning("%s: missing mindestanforderungen", path.name)
                additional_qualifications.append(
                    AdditionalQualification(
                        name=data["bezeichnung"],
                        definition=data.get("definition", ""),
                        requirements=data.get("mindestanforderungen", ""),
                        content=content,
                    )
                )
        except Exception as e:
            log.error("%s: %s", path.name, e)

    catalog = Catalog(
        medical_fields=list(medical_fields.values()),
        specialties=specialties,
        sub_specialties=sub_specialties,
        additional_qualifications=additional_qualifications,
    )
    log.info(
        "Catalog: %d fields, %d specialties, %d sub-specialties, "
        "%d additional qualifications",
        len(catalog.medical_fields),
        len(catalog.specialties),
        len(catalog.sub_specialties),
        len(catalog.additional_qualifications),
    )
    return catalog


# ============================================================================
# Step 4: Catalog -> DuckDB
# ============================================================================


def flatten_items(
    items: list[KnowledgeItem | SkillItem | CourseItem],
    section_id: int,
    counter: list[int],
    parent_id: int | None = None,
) -> list[dict]:
    """Recursively flatten competency items into DB rows."""
    rows: list[dict] = []
    for item in items:
        counter[0] += 1
        item_id = counter[0]
        rows.append(
            {
                "id": item_id,
                "section_id": section_id,
                "parent_item_id": parent_id,
                "type": item.type,
                "description": item.description,
                "target_number": getattr(item, "target_number", None),
            }
        )
        sub = getattr(item, "sub_items", None)
        if sub:
            rows.extend(flatten_items(sub, section_id, counter, item_id))
    return rows


def catalog_to_duckdb(catalog: Catalog, db_path: Path) -> None:
    """Write a Catalog into a normalized DuckDB database."""
    log.info("Writing %s", db_path)
    db_path.unlink(missing_ok=True)
    con = duckdb.connect(str(db_path))

    con.execute(
        "CREATE TABLE medical_fields ("
        "  id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL)"
    )
    con.execute(
        "CREATE TABLE specialties ("
        "  id INTEGER PRIMARY KEY, name VARCHAR NOT NULL,"
        "  medical_field_id VARCHAR NOT NULL REFERENCES medical_fields(id),"
        "  alternative_title VARCHAR, field_definition VARCHAR NOT NULL,"
        "  training_duration VARCHAR NOT NULL)"
    )
    con.execute(
        "CREATE TABLE sub_specialties ("
        "  id INTEGER PRIMARY KEY, name VARCHAR NOT NULL,"
        "  parent_specialty VARCHAR NOT NULL, alternative_title VARCHAR,"
        "  prerequisite VARCHAR NOT NULL, training_duration VARCHAR NOT NULL)"
    )
    con.execute(
        "CREATE TABLE additional_qualifications ("
        "  id INTEGER PRIMARY KEY, name VARCHAR NOT NULL,"
        "  definition VARCHAR NOT NULL, requirements VARCHAR NOT NULL)"
    )
    con.execute(
        "CREATE TABLE competency_sections ("
        "  id INTEGER PRIMARY KEY, owner_type VARCHAR NOT NULL,"
        "  owner_id INTEGER NOT NULL, name VARCHAR NOT NULL)"
    )
    con.execute(
        "CREATE TABLE competency_items ("
        "  id INTEGER PRIMARY KEY,"
        "  section_id INTEGER NOT NULL REFERENCES competency_sections(id),"
        "  parent_item_id INTEGER REFERENCES competency_items(id),"
        "  type VARCHAR NOT NULL, description VARCHAR NOT NULL,"
        "  target_number INTEGER)"
    )

    for mf in catalog.medical_fields:
        con.execute("INSERT INTO medical_fields VALUES (?, ?)", [mf.id, mf.name])

    sec_counter = 0
    item_counter = [0]

    def insert_sections(
        owner_type: str, owner_id: int, sections: list[CompetencySection]
    ) -> None:
        nonlocal sec_counter
        for section in sections:
            sec_counter += 1
            sid = sec_counter
            con.execute(
                "INSERT INTO competency_sections VALUES (?, ?, ?, ?)",
                [sid, owner_type, owner_id, section.name],
            )
            for row in flatten_items(section.items, sid, item_counter):
                con.execute(
                    "INSERT INTO competency_items VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        row["id"],
                        row["section_id"],
                        row["parent_item_id"],
                        row["type"],
                        row["description"],
                        row["target_number"],
                    ],
                )

    for i, s in enumerate(catalog.specialties, 1):
        con.execute(
            "INSERT INTO specialties VALUES (?, ?, ?, ?, ?, ?)",
            [
                i,
                s.name,
                s.medical_field_id,
                s.alternative_title,
                s.field_definition,
                s.training_duration,
            ],
        )
        insert_sections("specialty", i, s.content)

    for i, ss in enumerate(catalog.sub_specialties, 1):
        con.execute(
            "INSERT INTO sub_specialties VALUES (?, ?, ?, ?, ?, ?)",
            [
                i,
                ss.name,
                ss.parent_specialty,
                ss.alternative_title,
                ss.prerequisite,
                ss.training_duration,
            ],
        )
        insert_sections("sub_specialty", i, ss.content)

    for i, aq in enumerate(catalog.additional_qualifications, 1):
        con.execute(
            "INSERT INTO additional_qualifications VALUES (?, ?, ?, ?)",
            [i, aq.name, aq.definition, aq.requirements],
        )
        insert_sections("additional_qualification", i, aq.content)

    con.close()

    # Log summary
    con = duckdb.connect(str(db_path), read_only=True)
    for table in TABLES:
        count = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        log.info("  %s: %d rows", table, count)
    con.close()


# ============================================================================
# Export: DuckDB -> Catalog -> JSON / HTML
# ============================================================================


def duckdb_to_catalog(db_path: Path) -> Catalog:
    """Read a DuckDB database back into a Catalog object."""
    log.info("Reading catalog from %s", db_path)
    con = duckdb.connect(str(db_path), read_only=True)

    # Medical fields
    medical_fields = [
        MedicalField(id=r[0], name=r[1])
        for r in con.execute("SELECT id, name FROM medical_fields ORDER BY id").fetchall()
    ]

    def load_sections(owner_type: str, owner_id: int) -> list[CompetencySection]:
        sections: list[CompetencySection] = []
        sec_rows = con.execute(
            "SELECT id, name FROM competency_sections "
            "WHERE owner_type = ? AND owner_id = ? ORDER BY id",
            [owner_type, owner_id],
        ).fetchall()
        for sec_id, sec_name in sec_rows:
            items = _load_items(con, sec_id, parent_id=None)
            if items:
                sections.append(CompetencySection(name=sec_name, items=items))
        return sections

    # Specialties
    specialties = []
    for r in con.execute(
        "SELECT id, name, medical_field_id, alternative_title, "
        "field_definition, training_duration FROM specialties ORDER BY id"
    ).fetchall():
        specialties.append(
            Specialty(
                name=r[1],
                medical_field_id=r[2],
                alternative_title=r[3],
                field_definition=r[4],
                training_duration=r[5],
                content=load_sections("specialty", r[0]),
            )
        )

    # Sub-specialties
    sub_specialties = []
    for r in con.execute(
        "SELECT id, name, parent_specialty, alternative_title, "
        "prerequisite, training_duration FROM sub_specialties ORDER BY id"
    ).fetchall():
        sub_specialties.append(
            SubSpecialty(
                name=r[1],
                parent_specialty=r[2],
                alternative_title=r[3],
                prerequisite=r[4],
                training_duration=r[5],
                content=load_sections("sub_specialty", r[0]),
            )
        )

    # Additional qualifications
    additional_qualifications = []
    for r in con.execute(
        "SELECT id, name, definition, requirements "
        "FROM additional_qualifications ORDER BY id"
    ).fetchall():
        additional_qualifications.append(
            AdditionalQualification(
                name=r[1],
                definition=r[2],
                requirements=r[3],
                content=load_sections("additional_qualification", r[0]),
            )
        )

    con.close()
    return Catalog(
        medical_fields=medical_fields,
        specialties=specialties,
        sub_specialties=sub_specialties,
        additional_qualifications=additional_qualifications,
    )


def _load_items(
    con, section_id: int, parent_id: int | None
) -> list[KnowledgeItem | SkillItem | CourseItem]:
    """Recursively load competency items for a section."""
    rows = con.execute(
        "SELECT id, type, description, target_number "
        "FROM competency_items WHERE section_id = ? AND parent_item_id IS NOT DISTINCT FROM ? "
        "ORDER BY id",
        [section_id, parent_id],
    ).fetchall()

    items: list[KnowledgeItem | SkillItem | CourseItem] = []
    for item_id, typ, desc, target in rows:
        children = _load_items(con, section_id, item_id)
        sub = children or None
        if typ == "knowledge":
            items.append(
                KnowledgeItem(
                    description=desc, target_number=target, sub_items=sub
                )
            )
        elif typ == "skill":
            items.append(
                SkillItem(description=desc, target_number=target, sub_items=sub)
            )
        elif typ == "course":
            items.append(CourseItem(description=desc))
    return items


def export_json(catalog: Catalog, path: Path) -> None:
    """Write catalog as JSON."""
    path.write_text(
        json.dumps(catalog.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Wrote JSON: %s", path)


def _render_items_html(items: list[dict], depth: int = 0) -> str:
    """Render competency items as nested HTML list."""
    parts: list[str] = []
    for item in items:
        typ = item["type"]
        desc = item["description"]
        tn = item.get("target_number")
        badge = {"knowledge": "K", "skill": "S", "course": "C"}[typ]
        tn_str = f' <span class="rz">{tn}</span>' if tn else ""
        parts.append(f'<li><span class="{typ}">[{badge}]</span> {desc}{tn_str}')
        subs = item.get("sub_items")
        if subs:
            parts.append(f"<ul>{_render_items_html(subs, depth + 1)}</ul>")
        parts.append("</li>")
    return "\n".join(parts)


def export_html(catalog: Catalog, path: Path) -> None:
    """Write catalog as a single static HTML file with minimal CSS."""
    data = catalog.model_dump()
    parts: list[str] = []

    css = (
        "body{font-family:system-ui,sans-serif;max-width:60em;margin:2em auto;padding:0 1em;"
        "line-height:1.5;color:#1a1a1a}"
        "h1{font-size:1.4em}h2{font-size:1.2em;margin-top:2em}h3{font-size:1em;margin-top:1.5em}"
        "table{border-collapse:collapse;width:100%;margin:1em 0}"
        "th,td{text-align:left;padding:.3em .6em;border-bottom:1px solid #ddd}"
        "th{background:#f5f5f5}"
        "ul{padding-left:1.4em}li{margin:.2em 0}"
        ".knowledge{color:#1a5f7a}.skill{color:#2e7d32}.course{color:#6a1b9a}"
        ".rz{background:#e8e8e8;border-radius:3px;padding:0 .4em;font-size:.85em}"
        "details{margin:.5em 0}summary{cursor:pointer;font-weight:600}"
        "nav a{margin-right:1em}"
    )

    parts.append(
        "<!DOCTYPE html>\n<html lang='de'>\n<head><meta charset='utf-8'>\n"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>\n"
        f"<title>MWBO Katalog</title>\n<style>{css}</style>\n</head>\n<body>\n"
    )

    parts.append("<h1>MWBO 2018 Katalog</h1>\n")
    parts.append("<nav><a href='#fields'>Gebiete</a><a href='#specs'>Facharzt</a>"
                 "<a href='#sub'>Schwerpunkte</a><a href='#zusatz'>Zusatz-WB</a></nav>\n")

    # Medical fields overview
    parts.append("<h2 id='fields'>Gebiete</h2>\n<table><tr><th>ID</th><th>Name</th></tr>\n")
    for mf in data["medical_fields"]:
        parts.append(f"<tr><td>{mf['id']}</td><td>{mf['name']}</td></tr>\n")
    parts.append("</table>\n")

    # Specialties
    parts.append("<h2 id='specs'>Facharztkompetenzen</h2>\n")
    for s in data["specialties"]:
        parts.append(f"<h3>{s['name']}</h3>\n")
        if s.get("alternative_title"):
            parts.append(f"<p><em>{s['alternative_title']}</em></p>\n")
        parts.append(f"<p><strong>Gebiet:</strong> {s['medical_field_id']}</p>\n")
        parts.append(f"<p><strong>Definition:</strong> {s['field_definition'][:200]}{'…' if len(s['field_definition']) > 200 else ''}</p>\n")
        parts.append(f"<p><strong>Weiterbildungszeit:</strong> {s['training_duration'][:200]}{'…' if len(s['training_duration']) > 200 else ''}</p>\n")
        for sec in s["content"]:
            parts.append(f"<details><summary>{sec['name']} ({len(sec['items'])})</summary>\n")
            parts.append(f"<ul>{_render_items_html(sec['items'])}</ul>\n</details>\n")

    # Sub-specialties
    parts.append("<h2 id='sub'>Schwerpunktkompetenzen</h2>\n")
    for ss in data["sub_specialties"]:
        parts.append(f"<h3>{ss['name']}</h3>\n")
        parts.append(f"<p><strong>Voraussetzung:</strong> {ss['prerequisite'][:200]}</p>\n")
        parts.append(f"<p><strong>Weiterbildungszeit:</strong> {ss['training_duration'][:200]}</p>\n")
        for sec in ss["content"]:
            parts.append(f"<details><summary>{sec['name']} ({len(sec['items'])})</summary>\n")
            parts.append(f"<ul>{_render_items_html(sec['items'])}</ul>\n</details>\n")

    # Additional qualifications
    parts.append("<h2 id='zusatz'>Zusatz-Weiterbildungen</h2>\n")
    for aq in data["additional_qualifications"]:
        parts.append(f"<h3>{aq['name']}</h3>\n")
        parts.append(f"<p><strong>Definition:</strong> {aq['definition'][:200]}{'…' if len(aq['definition']) > 200 else ''}</p>\n")
        parts.append(f"<p><strong>Voraussetzungen:</strong> {aq['requirements'][:200]}{'…' if len(aq['requirements']) > 200 else ''}</p>\n")
        for sec in aq["content"]:
            parts.append(f"<details><summary>{sec['name']} ({len(sec['items'])})</summary>\n")
            parts.append(f"<ul>{_render_items_html(sec['items'])}</ul>\n</details>\n")

    parts.append("</body>\n</html>\n")

    path.write_text("".join(parts), encoding="utf-8")
    log.info("Wrote HTML: %s", path)


# ============================================================================
# Comparison
# ============================================================================


def compare_databases(db1_path: Path, db2_path: Path) -> None:
    """Compare two DuckDB databases and report differences."""
    log.info(
        "Comparing %s (original) vs %s (new)", db1_path.name, db2_path.name
    )

    con1 = duckdb.connect(str(db1_path), read_only=True)
    con2 = duckdb.connect(str(db2_path), read_only=True)

    all_match = True

    click.echo(f"\n{'=' * 60}")
    click.echo(f"Comparing {db1_path.name} (original) vs {db2_path.name} (new)")
    click.echo(f"{'=' * 60}")

    click.echo("\nRow counts:")
    for table in TABLES:
        c1 = con1.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        c2 = con2.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        match = "OK" if c1 == c2 else "DIFF"
        if c1 != c2:
            all_match = False
        click.echo(f"  {table:30s} {c1:>6} vs {c2:>6}  [{match}]")

    # Name-level diffs for entity tables
    for table in [
        "medical_fields",
        "specialties",
        "sub_specialties",
        "additional_qualifications",
    ]:
        names1 = set(
            r[0] for r in con1.execute(f"SELECT name FROM {table}").fetchall()
        )
        names2 = set(
            r[0] for r in con2.execute(f"SELECT name FROM {table}").fetchall()
        )

        only1 = names1 - names2
        only2 = names2 - names1
        if only1 or only2:
            all_match = False
            click.echo(f"\n  {table} name differences:")
            for n in sorted(only1):
                click.echo(f"    MISSING in new: {n}")
            for n in sorted(only2):
                click.echo(f"    EXTRA in new:   {n}")

    # Type distribution
    click.echo("\nCompetency items by type:")
    for label, con in [("original", con1), ("new     ", con2)]:
        rows = con.execute(
            "SELECT type, count(*) FROM competency_items GROUP BY type ORDER BY type"
        ).fetchall()
        dist = ", ".join(f"{t}: {c}" for t, c in rows)
        click.echo(f"  {label}: {dist}")

    # Competency items per owner_type
    click.echo("\nCompetency items per owner_type:")
    for label, con in [("original", con1), ("new     ", con2)]:
        rows = con.execute(
            "SELECT cs.owner_type, count(ci.id) "
            "FROM competency_sections cs "
            "JOIN competency_items ci ON ci.section_id = cs.id "
            "GROUP BY cs.owner_type ORDER BY cs.owner_type"
        ).fetchall()
        dist = ", ".join(f"{t}: {c}" for t, c in rows)
        click.echo(f"  {label}: {dist}")

    if all_match:
        click.echo("\nAll row counts match!")
    else:
        click.echo("\nDifferences found — see above.")

    con1.close()
    con2.close()


# ============================================================================
# CLI
# ============================================================================


def _setup_logging(verbose: int) -> None:
    level = logging.WARNING
    if verbose == 1:
        level = logging.INFO
    elif verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
        level=level,
    )


@click.group()
@click.option("-v", "--verbose", count=True, help="Increase verbosity (-v info, -vv debug).")
def cli(verbose: int) -> None:
    """MWBO pipeline: docx -> duckdb."""
    _setup_logging(verbose)


@cli.command()
@click.option(
    "--input",
    "docx_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=BASE_DIR / "mwbo.docx",
    show_default=True,
    help="Input docx file.",
)
@click.option(
    "--output",
    "output_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=TMP_DIR / "html",
    show_default=True,
    help="Output directory for HTML files.",
)
def split(docx_path: Path, output_dir: Path) -> None:
    """Step 1: Split docx into per-section HTML files."""
    paths = split_docx(docx_path, output_dir)
    click.echo(f"Wrote {len(paths)} HTML files to {output_dir}")


@cli.command()
@click.option(
    "--input",
    "html_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=TMP_DIR / "html",
    show_default=True,
    help="Directory with HTML files.",
)
@click.option(
    "--output",
    "json_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=TMP_DIR / "json",
    show_default=True,
    help="Output directory for JSON files.",
)
def parse(html_dir: Path, json_dir: Path) -> None:
    """Step 2: Parse HTML files into per-specialty JSON."""
    paths = parse_all_html(html_dir, json_dir)
    click.echo(f"Wrote {len(paths)} JSON files to {json_dir}")


@cli.command()
@click.option(
    "--input",
    "json_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=TMP_DIR / "json",
    show_default=True,
    help="Directory with per-specialty JSON files.",
)
@click.option(
    "--output",
    "-o",
    "db_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=BASE_DIR / "catalog2.duckdb",
    show_default=True,
    help="Output DuckDB path.",
)
@click.option(
    "--catalog-json",
    type=click.Path(dir_okay=False, path_type=Path),
    default=TMP_DIR / "catalog.json",
    show_default=True,
    help="Also write merged catalog JSON to this path.",
)
def build(json_dir: Path, db_path: Path, catalog_json: Path) -> None:
    """Step 3+4: Merge JSON files into catalog and write DuckDB."""
    catalog = build_catalog(json_dir)

    catalog_json.parent.mkdir(parents=True, exist_ok=True)
    catalog_json.write_text(
        json.dumps(catalog.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Wrote %s", catalog_json)

    catalog_to_duckdb(catalog, db_path)
    click.echo(f"Wrote {db_path}")


@cli.command()
@click.argument(
    "db1",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.argument(
    "db2",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def compare(db1: Path, db2: Path) -> None:
    """Compare two DuckDB databases table by table."""
    compare_databases(db1, db2)


@cli.command()
@click.option(
    "--input",
    "docx_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=BASE_DIR / "mwbo.docx",
    show_default=True,
    help="Input docx file.",
)
@click.option(
    "--output",
    "-o",
    "db_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=BASE_DIR / "catalog2.duckdb",
    show_default=True,
    help="Output DuckDB path.",
)
@click.option(
    "--json",
    "json_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Also export catalog as JSON.",
)
@click.option(
    "--html",
    "html_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Also export catalog as browsable HTML.",
)
@click.option(
    "--compare-with",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Compare output with this existing DuckDB after the run.",
)
@click.option(
    "--keep-tmp",
    is_flag=True,
    help="Keep tmp_data/ after the run (default: delete on success).",
)
def run(
    docx_path: Path,
    db_path: Path,
    json_path: Path | None,
    html_path: Path | None,
    compare_with: Path | None,
    keep_tmp: bool,
) -> None:
    """Run the full pipeline: docx -> HTML -> JSON -> DuckDB."""
    tmp_dir = TMP_DIR

    # Clean tmp_data
    if tmp_dir.exists():
        log.info("Cleaning %s", tmp_dir)
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir()

    try:
        # Step 1: docx -> HTML
        html_dir = tmp_dir / "html"
        split_docx(docx_path, html_dir)

        # Step 2: HTML -> JSON
        json_dir = tmp_dir / "json"
        parse_all_html(html_dir, json_dir)

        # Step 3: JSON -> Catalog
        catalog = build_catalog(json_dir)

        # Step 4: Catalog -> DuckDB
        catalog_to_duckdb(catalog, db_path)
        click.echo(f"\nWrote {db_path}")

        # Optional exports (derived from the catalog we just built)
        if json_path:
            export_json(catalog, json_path)
            click.echo(f"Wrote {json_path}")
        if html_path:
            export_html(catalog, html_path)
            click.echo(f"Wrote {html_path}")

        # Compare
        cmp_target = compare_with or (BASE_DIR / "catalog.duckdb")
        if cmp_target.exists() and cmp_target != db_path:
            compare_databases(cmp_target, db_path)

    finally:
        if not keep_tmp and tmp_dir.exists():
            log.info("Cleaning up %s", tmp_dir)
            shutil.rmtree(tmp_dir)


@cli.command("export")
@click.option(
    "--input",
    "-i",
    "db_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=BASE_DIR / "catalog2.duckdb",
    show_default=True,
    help="Input DuckDB database.",
)
@click.option(
    "--json",
    "json_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Export catalog as JSON (contains everything to reconstruct the DuckDB).",
)
@click.option(
    "--html",
    "html_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Export catalog as browsable HTML.",
)
def export_cmd(db_path: Path, json_path: Path | None, html_path: Path | None) -> None:
    """Export a DuckDB catalog to JSON and/or HTML."""
    if not json_path and not html_path:
        raise click.UsageError("Specify at least one of --json or --html.")
    catalog = duckdb_to_catalog(db_path)
    if json_path:
        export_json(catalog, json_path)
        click.echo(f"Wrote {json_path}")
    if html_path:
        export_html(catalog, html_path)
        click.echo(f"Wrote {html_path}")


if __name__ == "__main__":
    cli()
