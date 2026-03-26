#!/usr/bin/env python3
"""
Convert MWBO HTML files to structured JSON according to schema.
"""

import json
import re
from pathlib import Path
from typing import Optional, Dict, Any


def normalize_whitespace(text: str) -> str:
    """Normalize whitespace in text."""
    if not text:
        return text
    # Replace multiple spaces/newlines with single space
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def extract_richtzahl(text: str) -> Optional[int]:
    """Extract richtzahl (numerical value) from text."""
    if not text:
        return None
    # Look for numbers at word boundaries
    match = re.search(r'\b(\d+)\b', text)
    if match:
        return int(match.group(1))
    return None


def parse_html_file(filepath: Path) -> tuple:
    """Parse HTML file and extract main sections."""
    with open(filepath, 'r', encoding='utf-8') as f:
        html = f.read()

    # Extract H3 (Gebiet) - only text inside the tag, not nested HTML
    h3_match = re.search(r'<h3[^>]*>([^<]+)</h3>', html)
    h3_text = normalize_whitespace(h3_match.group(1)) if h3_match else ""

    # Extract H4 (Facharzt designation) - only text inside the tag (may span lines)
    h4_match = re.search(r'<h4[^>]*>(.*?)</h4>', html, re.DOTALL)
    if h4_match:
        h4_text = normalize_whitespace(re.sub(r'<[^>]+>', '', h4_match.group(1)))
    else:
        h4_text = ""

    # Extract blockquote after H4 (zusatzbezeichnung)
    blockquote_match = re.search(r'<h4[^>]*>.*?</h4>\s*<blockquote>\s*<p>\(([^)]+)\)', html, re.DOTALL)
    zusatzbezeichnung = blockquote_match.group(1).strip() if blockquote_match else None

    # Extract tables
    table_pattern = r'<table[^>]*>.*?</table>'
    tables = re.findall(table_pattern, html, re.DOTALL)

    return h3_text, h4_text, zusatzbezeichnung, tables


def extract_text_from_html(html_fragment: str) -> str:
    """Remove HTML tags and normalize text."""
    text = re.sub(r'<[^>]+>', '', html_fragment)
    return normalize_whitespace(text)


def parse_table_rows(table_html: str) -> list:
    """Parse rows from a table."""
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL)
    parsed_rows = []

    for row in rows:
        # Check for colspan
        colspan_match = re.search(r'colspan="(\d+)"', row)

        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.DOTALL)

        if not cells:
            continue

        row_data = {
            'cells': [extract_text_from_html(cell) for cell in cells],
            'is_section': bool(colspan_match)
        }

        parsed_rows.append(row_data)

    return parsed_rows


def convert_file(filepath: Path) -> Dict[str, Any]:
    """Convert a single HTML file to JSON structure."""
    h3_text, h4_text, zusatzbezeichnung, tables = parse_html_file(filepath)

    # Determine type and gebiet
    if 'Zusatz-Weiterbildung' in h3_text:
        typ = 'zusatz-weiterbildung'
        # Remove "Zusatz-Weiterbildung " from beginning
        gebiet = re.sub(r'^Zusatz-Weiterbildung\s+', '', h3_text).strip()
        bezeichnung = h3_text.strip()
    elif 'Schwerpunkt' in h4_text:
        typ = 'schwerpunkt'
        # Remove "Gebiet " from beginning of h3_text
        gebiet = re.sub(r'^Gebiet\s+', '', h3_text).strip()
        bezeichnung = h4_text.strip()
    else:
        typ = 'facharzt'
        # Remove "Gebiet " from beginning of h3_text
        gebiet = re.sub(r'^Gebiet\s+', '', h3_text).strip()
        bezeichnung = h4_text.strip()

    result = {
        'typ': typ,
        'gebiet': gebiet,
        'bezeichnung': bezeichnung,
    }

    if zusatzbezeichnung:
        result['zusatzbezeichnung'] = zusatzbezeichnung

    # Parse tables
    if tables:
        first_table_html = tables[0]

        # Extract Gebietsdefinition
        gebietsdefinition_match = re.search(
            r'<strong>Gebietsdefinition</strong>.*?<th>\s*<blockquote>\s*<p>(.*?)</p>\s*</blockquote>',
            first_table_html,
            re.DOTALL
        )
        if gebietsdefinition_match:
            result['gebietsdefinition'] = extract_text_from_html(gebietsdefinition_match.group(1))

        # Extract Weiterbildungszeit
        weiterbildungszeit_match = re.search(
            r'<strong>Weiterbildungszeit</strong>.*?<blockquote>\s*<p>(.*?)</p>',
            first_table_html,
            re.DOTALL
        )
        if weiterbildungszeit_match:
            result['weiterbildungszeit'] = extract_text_from_html(weiterbildungszeit_match.group(1))

        # Parse content tables (skip first table which has definitions)
        content_tables = tables[1:] if len(tables) > 1 else []
        inhalte = []

        for table_html in content_tables:
            parsed_rows = parse_table_rows(table_html)

            for row_data in parsed_rows:
                cells = row_data['cells']
                is_section = row_data['is_section']

                if is_section and cells:
                    # Section header
                    abschnitt_text = cells[0] if cells else ""
                    # Skip generic section headers
                    if abschnitt_text and 'Allgemeine Inhalte' not in abschnitt_text:
                        inhalte.append({'abschnitt': abschnitt_text})
                elif len(cells) >= 2:
                    # Regular row - check format
                    entry = {}

                    if cells[0]:
                        entry['kognitive_und_methodenkompetenz'] = cells[0]
                    if cells[1]:
                        entry['handlungskompetenz'] = cells[1]

                    # Extract richtzahl from third column
                    if len(cells) >= 3:
                        richtzahl = extract_richtzahl(cells[2])
                        if richtzahl is not None:
                            entry['richtzahl'] = richtzahl

                    if entry:
                        inhalte.append(entry)

        if inhalte:
            result['inhalte'] = inhalte

    return result


def main():
    """Main conversion function."""
    base_dir = Path('/home/tom/Projects/health_bot/gebiete')

    # Get all HTML files in directory
    html_files = sorted(base_dir.glob('gebiet-*.html'))

    for filepath in html_files:
        filename = filepath.name
        print(f"Converting {filename}...")

        try:
            data = convert_file(filepath)

            # Save as JSON
            json_filename = filename.replace('.html', '.json')
            json_path = base_dir / json_filename

            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            print(f"  -> Saved to {json_filename}")
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()


if __name__ == '__main__':
    main()
