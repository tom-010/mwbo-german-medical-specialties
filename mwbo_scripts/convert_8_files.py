#!/usr/bin/env python3
"""Convert 8 Zusatz-Weiterbildung HTML files to schema-valid JSON.

Critical rules:
- All 8 files use typ = "zusatz-weiterbildung"
- gebiet = H3 text minus "Zusatz-Weiterbildung " prefix
- bezeichnung = full H3 text (whitespace-normalized)
- definition from thead of 2-col info table
- mindestanforderungen from tbody of 2-col info table
- colspan=3 bold rows in 3-col tables: section headings only, no standalone items
- Every Inhalt MUST have abschnitt; richtzahl is int only
"""

import json
import re
import sys
from pathlib import Path


def normalize(text: str) -> str:
    """Collapse whitespace and strip."""
    return re.sub(r'\s+', ' ', text).strip()


def strip_tags(html: str) -> str:
    """Remove HTML tags and return plain text (whitespace-normalized)."""
    return normalize(re.sub(r'<[^>]+>', ' ', html))


def parse_richtzahl(text: str) -> int | None:
    """Parse a richtzahl string to int, handling thousands separators."""
    if not text:
        return None
    # Remove periods/commas used as thousands separators
    clean = re.sub(r'[.,]', '', text.strip())
    m = re.search(r'\d+', clean)
    return int(m.group(0)) if m else None


def parse_html(content: str) -> dict:
    # ---------- H3: bezeichnung + gebiet ----------
    h3_m = re.search(r'<h3[^>]*>(.*?)</h3>', content, re.DOTALL)
    bezeichnung = normalize(strip_tags(h3_m.group(1))) if h3_m else ''
    gebiet = re.sub(r'^Zusatz-Weiterbildung\s+', '', bezeichnung)

    result: dict = {
        'typ': 'zusatz-weiterbildung',
        'gebiet': gebiet,
        'bezeichnung': bezeichnung,
    }

    # ---------- First 2-column info table ----------
    # Definition is in thead, Mindestanforderungen in tbody
    all_tables = list(re.finditer(r'<table(?:\s[^>]*)?>.*?</table>', content, re.DOTALL))

    info_table_html = None
    content_tables = []

    for tm in all_tables:
        tbl = tm.group(0)
        col_count = len(re.findall(r'<col\b', tbl))
        if col_count == 2 and info_table_html is None:
            info_table_html = tbl
        else:
            content_tables.append(tbl)

    if info_table_html:
        # Definition: in thead, second th cell
        thead_m = re.search(r'<thead>(.*?)</thead>', info_table_html, re.DOTALL)
        if thead_m:
            ths = re.findall(r'<th[^>]*>(.*?)</th>', thead_m.group(1), re.DOTALL)
            if len(ths) >= 2:
                result['definition'] = strip_tags(ths[1])

        # Mindestanforderungen: in tbody, second td cell
        tbody_m = re.search(r'<tbody>(.*?)</tbody>', info_table_html, re.DOTALL)
        if tbody_m:
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tbody_m.group(1), re.DOTALL)
            for row in rows:
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
                if len(cells) >= 2:
                    label = strip_tags(cells[0])
                    if 'Mindestanforderungen' in label:
                        result['mindestanforderungen'] = strip_tags(cells[1])

    # ---------- Content tables ----------
    inhalte = []
    current_abschnitt = None

    for tbl in content_tables:
        col_count = len(re.findall(r'<col\b', tbl))

        tbody_m = re.search(r'<tbody>(.*?)</tbody>', tbl, re.DOTALL)
        if not tbody_m:
            continue

        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tbody_m.group(1), re.DOTALL)

        for row in rows:
            # Section heading: colspan=3 with bold text
            cs3 = re.search(r'<td[^>]*\bcolspan="3"[^>]*>(.*?)</td>', row, re.DOTALL)
            if cs3:
                cell_html = cs3.group(1)
                if '<strong>' in cell_html:
                    current_abschnitt = strip_tags(cell_html)
                # Never emit a standalone heading item
                continue

            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)

            if col_count == 3 or len(cells) == 3:
                if len(cells) < 3:
                    continue
                kmk = strip_tags(cells[0])
                hk = strip_tags(cells[1])
                rz = parse_richtzahl(strip_tags(cells[2]))

                if not kmk and not hk and rz is None:
                    continue

                entry: dict = {'abschnitt': current_abschnitt or 'Allgemeines'}
                if kmk:
                    entry['kognitive_und_methodenkompetenz'] = kmk
                if hk:
                    entry['handlungskompetenz'] = hk
                if rz is not None:
                    entry['richtzahl'] = rz
                inhalte.append(entry)

            elif len(cells) == 1:
                # 1-column Kursinhalte style
                cell_html = cells[0]
                text = strip_tags(cell_html)
                if not text:
                    continue
                if '<strong>' in cell_html:
                    current_abschnitt = text
                    continue
                entry = {
                    'abschnitt': current_abschnitt or 'Allgemeines',
                    'text': text,
                }
                inhalte.append(entry)

    result['inhalte'] = inhalte
    return result


def build_ordered(data: dict) -> dict:
    """Return dict with keys in canonical schema order."""
    order = [
        'typ', 'gebiet', 'bezeichnung', 'zusatzbezeichnung',
        'gebietsdefinition', 'definition', 'voraussetzung',
        'mindestanforderungen', 'weiterbildungszeit', 'inhalte',
    ]
    out = {}
    for k in order:
        if k in data:
            out[k] = data[k]
    for k in data:
        if k not in out:
            out[k] = data[k]
    return out


FILES = [
    'zusatz-weiterbildung-allergologie.html',
    'zusatz-weiterbildung-andrologie.html',
    'zusatz-weiterbildung-betriebsmedizin.html',
    'zusatz-weiterbildung-dermatopathologie.html',
    'zusatz-weiterbildung-diabetologie.html',
    'zusatz-weiterbildung-geriatrie.html',
    'zusatz-weiterbildung-gynaekologische-exfoliativ-zytologie.html',
    'zusatz-weiterbildung-kinder-und-jugend-orthopaedie.html',
]


def main():
    base = Path('/home/tom/Projects/health_bot/gebiete')
    ok = 0
    fail = 0
    for fname in FILES:
        html_path = base / fname
        json_path = base / fname.replace('.html', '.json')
        try:
            content = html_path.read_text(encoding='utf-8')
            data = parse_html(content)
            ordered = build_ordered(data)
            json_path.write_text(
                json.dumps(ordered, ensure_ascii=False, indent=2),
                encoding='utf-8'
            )
            n = len(data.get('inhalte', []))
            print(f'OK  {fname} ({n} inhalte)')
            ok += 1
        except Exception as e:
            import traceback
            print(f'ERR {fname}: {e}', file=sys.stderr)
            traceback.print_exc()
            fail += 1
    print(f'\n{ok} succeeded, {fail} failed')


if __name__ == '__main__':
    main()
