#!/usr/bin/env python3
"""
Parse MWBO HTML files and convert to structured JSON.
"""
import re
import json
from pathlib import Path
from typing import Dict, Any, List

def extract_text(html_str: str) -> str:
    """Extract plain text from HTML, preserving structure."""
    # Remove tags but keep content
    text = re.sub(r'<[^>]+>', '', html_str)
    # Normalize whitespace - replace newlines with space
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def parse_mwbo_html(filepath: Path) -> Dict[str, Any]:
    """Parse HTML file and extract structured data."""
    with open(filepath, 'r', encoding='utf-8') as f:
        html_content = f.read()

    data = {}

    # Extract H3 (Gebiet)
    h3_match = re.search(r'<h3[^>]*>(.*?)</h3>', html_content, re.DOTALL)
    if h3_match:
        h3_text = extract_text(h3_match.group(1))
        data['gebiet'] = h3_text.replace('Gebiet ', '').strip()

    # Extract H4 if it exists
    h4_match = re.search(r'<h4[^>]*>(.*?)</h4>', html_content, re.DOTALL)
    h4_text = ""
    if h4_match:
        h4_text = extract_text(h4_match.group(1))

    # Extract blockquote after H4 or H3 (for Schwerpunkt/zusatzbezeichnung)
    blockquote_match = re.search(r'</h4>\s*<blockquote>\s*<p>\(([^)]+)\)', html_content)
    if not blockquote_match:
        blockquote_match = re.search(r'</h3>\s*<blockquote>\s*<p>.*?\(([^)]+)\)', html_content, re.DOTALL)

    zusatz_bez = None
    if blockquote_match:
        zusatz_bez = blockquote_match.group(1).strip()

    # Check for Schwerpunkt in blockquote after H3 (alternative format)
    schwerpunkt_match = re.search(
        r'</h3>\s*<blockquote>\s*<p>.*?<strong>Schwerpunkt\s+([^<]+)</strong>',
        html_content, re.DOTALL
    )
    if schwerpunkt_match and not h4_text:
        h4_text = "Schwerpunkt " + extract_text(schwerpunkt_match.group(1))

    # Determine type
    data['typ'] = 'facharzt'

    if h4_text:
        if 'Zusatz-Weiterbildung' in h4_text:
            data['typ'] = 'zusatz-weiterbildung'
        elif 'Schwerpunkt' in h4_text:
            data['typ'] = 'schwerpunkt'

        data['bezeichnung'] = h4_text

    if zusatz_bez:
        data['zusatzbezeichnung'] = zusatz_bez

    # Parse first table (2-column, with varying widths)
    first_table = re.search(
        r'<table>\s*<colgroup>\s*<col[^>]*style="width: [0-9]+%"[^>]*/>\s*<col[^>]*style="width: [0-9]+%"[^>]*/>\s*</colgroup>(.*?)</table>',
        html_content, re.DOTALL
    )

    if first_table:
        table_content = first_table.group(1)

        # Parse both thead and tbody
        all_rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_content, re.DOTALL)

        for row in all_rows:
            # Check for th cells (header) or td cells (body)
            # Extract header (label) from first cell
            header_match = re.search(r'<(?:td|th)[^>]*>.*?<strong>([^<]+)</strong>', row, re.DOTALL)
            if not header_match:
                continue

            header = header_match.group(1).strip()

            # Extract all th or td cells
            cells = re.findall(r'<(?:th|td)[^>]*>(.*?)</(?:th|td)>', row, re.DOTALL)

            if len(cells) < 2:
                continue

            # Second cell contains the content
            content_html = cells[1]
            content = extract_text(content_html)

            if 'Gebietsdefinition' in header:
                data['gebietsdefinition'] = content
            elif 'Definition' in header:
                data['definition'] = content
            elif 'Voraussetzung' in header:
                data['voraussetzung'] = content
            elif 'Mindestanforderungen' in header:
                data['mindestanforderungen'] = content
            elif 'Weiterbildungszeit' in header:
                data['weiterbildungszeit'] = content

    # Parse 3-column content tables
    inhalte = []

    # Find all 3-column tables
    tables = re.findall(
        r'<table[^>]*>\s*<colgroup>\s*<col[^>]*style="width: 44%"[^>]*/>\s*<col[^>]*style="width: 44%"[^>]*/>\s*<col[^>]*style="width: 11%"[^>]*/>\s*</colgroup>.*?<tbody>(.*?)</tbody>\s*</table>',
        html_content, re.DOTALL
    )

    for tbody_content in tables:
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tbody_content, re.DOTALL)

        for row in rows:
            # Check for colspan="3" (section headings or empty rows)
            if '<td colspan="3"' in row:
                # Extract section title (text in strong tag)
                section_match = re.search(r'<td colspan="3"[^>]*>.*?<strong>([^<]+)</strong>', row, re.DOTALL)
                if section_match:
                    section_text = extract_text(section_match.group(1))
                    inhalte.append({
                        "abschnitt": section_text
                    })
                # Skip this row (either empty or already processed as section)
                continue

            # Skip empty rows
            cell_content = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
            if not any(extract_text(cell).strip() for cell in cell_content):
                continue

            # Extract cells
            cells = cell_content

            if len(cells) >= 3:
                col1_text = extract_text(cells[0])
                col2_text = extract_text(cells[1])
                col3_text = extract_text(cells[2])

                # Build item - only if at least one column has content
                if col1_text or col2_text or col3_text:
                    item = {}

                    if col1_text:
                        item['kognitive_und_methodenkompetenz'] = col1_text

                    if col2_text:
                        item['handlungskompetenz'] = col2_text

                    if col3_text and col3_text.isdigit():
                        item['richtzahl'] = int(col3_text)

                    # Only add if has meaningful content
                    if item:
                        inhalte.append(item)

    if inhalte:
        data['inhalte'] = inhalte

    return data


def clean_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Remove null/empty values and normalize."""
    cleaned = {}
    for key, value in data.items():
        if value is None or value == "" or (isinstance(value, list) and not value):
            continue
        if isinstance(value, str):
            # Normalize whitespace (including newlines)
            value = re.sub(r'\s+', ' ', value).strip()
            if not value:
                continue
        cleaned[key] = value
    return cleaned


def main():
    base_dir = Path('/home/tom/Projects/health_bot/gebiete')

    files = [
        'gebiet-chirurgie-fuer-orthopaedie-und-unfallchirurgie.html',
        'gebiet-chirurgie-fuer-plastische-rekonstruktive-und-aesthetische-chirurgie.html',
        'gebiet-chirurgie-fuer-viszeralchirurgie.html',
        'gebiet-chirurgie-gefaesschirurgie.html',
        'gebiet-chirurgie-herzchirurgie.html',
        'gebiet-chirurgie-thoraxchirurgie.html',
        'gebiet-frauenheilkunde-und-geburtshilfe.html',
        'gebiet-frauenheilkunde-und-geburtshilfe-schwerpunkt-gynaekologische-endokrinologie-und-reproduktionsmedizin.html',
        'gebiet-frauenheilkunde-und-geburtshilfe-schwerpunkt-gynaekologische-onkologie.html',
        'gebiet-frauenheilkunde-und-geburtshilfe-schwerpunkt-spezielle-geburtshilfe-und-perinatalmedizin.html',
        'gebiet-hals-nasen-ohrenheilkunde.html',
        'gebiet-haut-und-geschlechtskrankheiten.html',
    ]

    for filename in files:
        filepath = base_dir / filename
        if not filepath.exists():
            print(f"File not found: {filepath}")
            continue

        print(f"Processing: {filename}")
        data = parse_mwbo_html(filepath)
        data = clean_data(data)

        # Write JSON
        json_filepath = filepath.with_suffix('.json')
        with open(json_filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"  -> {json_filepath.name}")


if __name__ == '__main__':
    main()
