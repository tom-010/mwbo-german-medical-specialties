#!/usr/bin/env python3
"""
Convert MWBO Zusatz-Weiterbildung HTML files to JSON format.

Schema:
{
  "typ": "zusatz-weiterbildung",
  "gebiet": "from H3 minus 'Zusatz-Weiterbildung '",
  "bezeichnung": "full H3 text",
  "definition": "from Definition thead row",
  "mindestanforderungen": "from Mindestanforderungen tbody row",
  "inhalte": [
    {"abschnitt": "heading"},
    {"abschnitt": "...", "kognitive_und_methodenkompetenz": "col1", "handlungskompetenz": "col2", "richtzahl": int}
  ]
}
"""

import json
import re
from pathlib import Path
from bs4 import BeautifulSoup


def normalize_whitespace(text):
    """Normalize whitespace in text."""
    if not text:
        return None
    text = re.sub(r'\s+', ' ', text).strip()
    return text if text else None


def parse_html(html_path):
    """Parse HTML file and extract content."""
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    soup = BeautifulSoup(content, 'html.parser')

    # Extract H3 title
    h3 = soup.find('h3')
    if not h3:
        return None

    full_title = normalize_whitespace(h3.get_text())
    gebiet = full_title.replace('Zusatz-Weiterbildung ', '').strip() if full_title else None

    # Find all tables
    tables = soup.find_all('table')

    if len(tables) < 2:
        return None

    # First table: Definition and Mindestanforderungen
    first_table = tables[0]
    definition = None
    mindestanforderungen = None

    rows = first_table.find_all('tr')
    for row in rows:
        ths = row.find_all('th')
        if ths and len(ths) >= 2:
            # Header row with Definition
            if 'Definition' in ths[0].get_text():
                definition = normalize_whitespace(ths[1].get_text())
        tds = row.find_all('td')
        if tds and len(tds) >= 2 and 'Mindestanforderungen' in tds[0].get_text():
            mindestanforderungen = normalize_whitespace(tds[1].get_text())

    # Parse content tables (from 2nd table onwards)
    inhalte = []

    # Process each content table starting from second table
    for table_idx in range(1, len(tables)):
        table = tables[table_idx]
        rows = table.find_all('tr')

        if not rows:
            continue

        # Process data rows (skip header row which is the first)
        for row_idx, row in enumerate(rows[1:], 1):
            tds = row.find_all('td')
            if not tds:
                continue

            num_cols = len(tds)
            
            # Get colspan attribute of first cell
            colspan = tds[0].get('colspan')

            # Section heading (colspan=3 in 3-col table)
            if colspan == '3' and num_cols == 1:
                cell_text = normalize_whitespace(tds[0].get_text())
                if cell_text:
                    inhalte.append({
                        "abschnitt": cell_text
                    })
            elif num_cols == 3:
                # 3-col table (standard format with 3 columns)
                col1_text = normalize_whitespace(tds[0].get_text())
                col2_text = normalize_whitespace(tds[1].get_text())
                col3_text = normalize_whitespace(tds[2].get_text())

                entry = {}

                # Set abschnitt: preferentially from column 1, then column 2
                abschnitt = col1_text or col2_text

                # Add column 1 only if it has content and differs from abschnitt
                if col1_text and col1_text != abschnitt:
                    entry["kognitive_und_methodenkompetenz"] = col1_text
                elif col1_text and col1_text == abschnitt and (col2_text or col3_text):
                    # Only add if it's truly different content
                    entry["kognitive_und_methodenkompetenz"] = col1_text

                # Add column 2 only if it has content
                if col2_text:
                    entry["handlungskompetenz"] = col2_text

                if col3_text:
                    # Try to convert to int if it's a number
                    if col3_text.isdigit():
                        entry["richtzahl"] = int(col3_text)

                # Set abschnitt 
                if abschnitt:
                    final_entry = {"abschnitt": abschnitt}
                    final_entry.update(entry)
                    entry = final_entry

                # Only add if there's content beyond just abschnitt
                if len(entry) > 1 or (len(entry) == 1 and 'abschnitt' in entry):
                    inhalte.append(entry)

    result = {
        "typ": "zusatz-weiterbildung",
        "gebiet": gebiet,
        "bezeichnung": full_title,
        "definition": definition,
        "mindestanforderungen": mindestanforderungen,
        "inhalte": inhalte
    }

    # Remove null and empty fields, and recursively remove null from nested dicts
    def clean_dict(d):
        cleaned = {}
        for k, v in d.items():
            if v is None or v == []:
                continue
            if isinstance(v, list):
                cleaned[k] = [clean_dict(item) if isinstance(item, dict) else item for item in v]
            elif isinstance(v, dict):
                cleaned[k] = clean_dict(v)
            else:
                cleaned[k] = v
        return cleaned

    result = clean_dict(result)
    return result


def convert_file(input_path, output_path):
    """Convert a single HTML file to JSON."""
    try:
        data = parse_html(input_path)
        if data:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"✓ {input_path.name} -> {output_path.name}")
            return True
        else:
            print(f"✗ Failed to parse {input_path.name}")
            return False
    except Exception as e:
        print(f"✗ Error converting {input_path.name}: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    base_path = Path('/home/tom/Projects/health_bot/gebiete')

    files = [
        'zusatz-weiterbildung-haemostaseologie.html',
        'zusatz-weiterbildung-handchirurgie.html',
        'zusatz-weiterbildung-immunologie.html',
        'zusatz-weiterbildung-infektiologie.html',
        'zusatz-weiterbildung-intensivmedizin.html',
        'zusatz-weiterbildung-kardiale-magnetresonanztomographie.html',
    ]

    success_count = 0
    for filename in files:
        input_file = base_path / filename
        if not input_file.exists():
            print(f"✗ File not found: {filename}")
            continue
        output_file = input_file.with_suffix('.json')

        if convert_file(input_file, output_file):
            success_count += 1

    print(f"\nCompleted: {success_count}/{len(files)} files converted")


if __name__ == '__main__':
    main()
