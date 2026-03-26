#!/usr/bin/env python3
"""Convert 8 new MWBO HTML files to schema-valid JSON."""

import json
import re
import sys
from pathlib import Path


def normalize(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()


def strip_tags(html: str) -> str:
    return normalize(re.sub(r'<[^>]+>', ' ', html))


def is_bold_cell(html: str) -> bool:
    return '<strong>' in html


def parse_richtzahl(text: str):
    """Parse German number (may use . as thousands sep) to int or None."""
    text = text.strip()
    if not text:
        return None
    # Remove German thousands separator
    cleaned = text.replace('.', '').replace(',', '')
    if cleaned.isdigit():
        return int(cleaned)
    # Try extracting first number
    m = re.match(r'^(\d+)', text.replace('.', ''))
    if m:
        return int(m.group(1))
    return None


def parse_html(content: str) -> dict:
    result = {}

    # H3: gebiet
    h3 = re.search(r'<h3[^>]*>(.*?)</h3>', content, re.DOTALL)
    if h3:
        text = strip_tags(h3.group(1))
        if text.startswith('Gebiet '):
            text = text[len('Gebiet '):]
        result['gebiet'] = text

    # H4: bezeichnung + typ
    h4 = re.search(r'<h4[^>]*>(.*?)</h4>', content, re.DOTALL)
    if h4:
        bezeichnung = strip_tags(h4.group(1))
        result['bezeichnung'] = bezeichnung
        if bezeichnung.startswith('Schwerpunkt'):
            result['typ'] = 'schwerpunkt'
        elif 'Zusatz-Weiterbildung' in bezeichnung:
            result['typ'] = 'zusatz-weiterbildung'
        else:
            result['typ'] = 'facharzt'
    else:
        result['typ'] = 'facharzt'

    # zusatzbezeichnung: blockquote (parenthesized) immediately after h4
    zb = re.search(
        r'</h4>\s*<blockquote>\s*<p>\(([^)]+)\)</p>\s*</blockquote>',
        content
    )
    if zb:
        result['zusatzbezeichnung'] = normalize(zb.group(1))

    # All tables
    all_tables = list(re.finditer(r'<table[^>]*>.*?</table>', content, re.DOTALL))
    if not all_tables:
        result['inhalte'] = []
        return result

    # First table: definition/metadata
    first_tbl = all_tables[0].group(0)

    # gebietsdefinition (facharzt): from thead 2nd th
    ths = re.findall(r'<th[^>]*>(.*?)</th>', first_tbl, re.DOTALL)
    for i, th in enumerate(ths):
        th_text = strip_tags(th)
        if 'Gebietsdefinition' in th_text and i + 1 < len(ths):
            result['gebietsdefinition'] = strip_tags(ths[i + 1])
            break

    # voraussetzung (schwerpunkt): single colspan=2 th
    if result.get('typ') == 'schwerpunkt':
        thead_m = re.search(r'<thead>(.*?)</thead>', first_tbl, re.DOTALL)
        if thead_m:
            thead_txt = strip_tags(thead_m.group(1))
            if thead_txt and 'Gebietsdefinition' not in thead_txt:
                result['voraussetzung'] = thead_txt

    # weiterbildungszeit from first table tbody
    tbody_m = re.search(r'<tbody>(.*?)</tbody>', first_tbl, re.DOTALL)
    if tbody_m:
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tbody_m.group(1), re.DOTALL)
        for row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            if len(cells) >= 2:
                label = strip_tags(cells[0])
                if 'Weiterbildungszeit' in label:
                    result['weiterbildungszeit'] = strip_tags(cells[1])
                    break
            elif len(cells) == 1:
                # weiterbildungszeit may be in a single cell for schwerpunkt
                txt = strip_tags(cells[0])
                if 'Weiterbildungszeit' not in txt and txt:
                    result.setdefault('weiterbildungszeit', txt)

    # Content tables (all after first)
    inhalte = []
    current_abschnitt = None

    for tbl_match in all_tables[1:]:
        tbl = tbl_match.group(0)

        # Skip glossary tables (2-column, no 3 cols)
        col_count = len(re.findall(r'<col\s', tbl))

        tbody_m = re.search(r'<tbody>(.*?)</tbody>', tbl, re.DOTALL)
        if not tbody_m:
            continue

        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tbody_m.group(1), re.DOTALL)

        for row in rows:
            # colspan=3 heading row
            colspan3 = re.search(r'<td[^>]*colspan="3"[^>]*>(.*?)</td>', row, re.DOTALL)
            if colspan3:
                cell_html = colspan3.group(1)
                text = strip_tags(cell_html)
                if text and is_bold_cell(cell_html):
                    current_abschnitt = text
                # Never emit standalone item
                continue

            # Regular cells
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)

            if col_count == 3 or len(cells) == 3:
                if len(cells) < 3:
                    continue
                kognitive = strip_tags(cells[0])
                handlung = strip_tags(cells[1])
                richtzahl_text = strip_tags(cells[2])

                if not kognitive and not handlung and not richtzahl_text:
                    continue

                if current_abschnitt is None:
                    continue

                entry = {'abschnitt': current_abschnitt}
                if kognitive:
                    entry['kognitive_und_methodenkompetenz'] = kognitive
                if handlung:
                    entry['handlungskompetenz'] = handlung
                if richtzahl_text:
                    rz = parse_richtzahl(richtzahl_text)
                    if rz is not None:
                        entry['richtzahl'] = rz

                inhalte.append(entry)

            elif len(cells) == 2 and col_count == 2:
                # glossary or 2-col table — skip for inhalte
                pass

    result['inhalte'] = inhalte

    # Clean up empty/None values (except inhalte which can be empty list)
    cleaned = {}
    for k, v in result.items():
        if k == 'inhalte':
            cleaned[k] = v
        elif v is not None and v != '':
            cleaned[k] = v

    return cleaned


def build_ordered(data: dict) -> dict:
    order = [
        'typ', 'gebiet', 'bezeichnung', 'zusatzbezeichnung',
        'gebietsdefinition', 'definition', 'voraussetzung',
        'mindestanforderungen', 'weiterbildungszeit', 'inhalte'
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
    'gebiet-pathologie-pathologie.html',
    'gebiet-pharmakologie-fuer-klinische-pharmakologie.html',
    'gebiet-pharmakologie-fuer-pharmakologie-und-toxikologie.html',
    'gebiet-phoniatrie-und-paedaudiologie.html',
    'gebiet-physikalische-und-rehabilitative-medizin.html',
    'gebiet-physiologie.html',
    'gebiet-psychiatrie-und-psychotherapie-fuer-psychiatrie-und-psychotherapie.html',
    'gebiet-psychiatrie-und-psychotherapie-schwerpunkt-forensische-psychiatrie.html',
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
            print(f'OK  {fname}')
            ok += 1
        except Exception as e:
            import traceback
            print(f'ERR {fname}: {e}', file=sys.stderr)
            traceback.print_exc()
            fail += 1
    print(f'\n{ok} succeeded, {fail} failed')


if __name__ == '__main__':
    main()
