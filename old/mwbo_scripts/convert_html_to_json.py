#!/usr/bin/env python3

import json
import re
from pathlib import Path
from typing import Optional, Dict, List, Any

def extract_text_from_cell(cell_html: str) -> str:
    """Extract and clean text from HTML cell content."""
    # Remove HTML tags but keep text content
    text = re.sub(r'<[^>]+>', '', cell_html)
    # Clean up whitespace
    text = ' '.join(text.split())
    return text.strip()

def parse_html_file(filepath: str) -> Dict[str, Any]:
    """Parse a single HTML file and convert to JSON structure."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # Extract H3 title
    h3_match = re.search(r'<h3[^>]*>([^<]+(?:<[^>]+>[^<]*)*)</h3>', content)
    if not h3_match:
        raise ValueError(f"No H3 title found in {filepath}")

    h3_text = extract_text_from_cell(h3_match.group(1))

    # Extract gebiet name from H3
    # Format: "Zusatz-Weiterbildung XXX" -> extract XXX
    gebiet_match = re.search(r'Zusatz-Weiterbildung\s+(.+)', h3_text)
    gebiet = gebiet_match.group(1) if gebiet_match else h3_text

    # Initialize result structure
    result = {
        "typ": "zusatz-weiterbildung",
        "gebiet": gebiet,
        "bezeichnung": h3_text,
    }

    # Extract Definition from first table's thead
    def_match = re.search(r'<table[^>]*>.*?<thead>(.*?)</thead>.*?<tbody>(.*?)</tbody>.*?</table>', content, re.DOTALL)
    if def_match:
        thead_content = def_match.group(1)
        tbody_content = def_match.group(2)

        # Find Definition in thead
        def_cell_match = re.search(r'<th>.*?<strong>Definition</strong>.*?</blockquote>\s*</th>\s*<th>(.*?)</th>', thead_content, re.DOTALL)
        if def_cell_match:
            definition_html = def_cell_match.group(1)
            result["definition"] = extract_text_from_cell(definition_html)

        # Find Mindestanforderungen in tbody
        min_req_match = re.search(r'<td>.*?<strong>Mindestanforderungen.*?</strong>.*?</blockquote>\s*</td>\s*<td>(.*?)</td>', tbody_content, re.DOTALL)
        if min_req_match:
            min_html = min_req_match.group(1)
            result["mindestanforderungen"] = extract_text_from_cell(min_html)

    # Extract weiterbildungszeit if present
    weiterbildungszeit_match = re.search(r'<blockquote>\s*<p><strong>Weiterbildungszeit</strong></p>(.*?)</blockquote>', content, re.DOTALL)
    if weiterbildungszeit_match:
        result["weiterbildungszeit"] = extract_text_from_cell(weiterbildungszeit_match.group(1))

    # Extract content tables (3-col or 1-col tables with Kognitive und Methodenkompetenz or Kursinhalte)
    inhalte = []

    # Find all tables
    table_matches = list(re.finditer(r'<table[^>]*>(.*?)</table>', content, re.DOTALL))

    # Process each table after the first one (first is definition/requirements)
    for table_idx, table_match in enumerate(table_matches[1:], 1):
        table_html = table_match.group(1)

        # Check if this is a 1-col table (Kursinhalte)
        # Look for single column with 100% width
        colgroup_match = re.search(r'<col\s+style="width:\s*100\s*%"', table_html)
        is_single_col = colgroup_match is not None

        if is_single_col:
            # 1-col table: thead = section title, bold tbody rows = sub-headings
            thead_match = re.search(r'<thead>(.*?)</thead>', table_html, re.DOTALL)
            thead_text = ""
            if thead_match:
                thead_text = extract_text_from_cell(thead_match.group(1))

            tbody_match = re.search(r'<tbody>(.*?)</tbody>', table_html, re.DOTALL)
            if tbody_match:
                tbody_html = tbody_match.group(1)
                # Parse each row
                row_pattern = r'<tr[^>]*>(.*?)</tr>'
                for row_match in re.finditer(row_pattern, tbody_html, re.DOTALL):
                    row_html = row_match.group(1)
                    cell_match = re.search(r'<td>(.*?)</td>', row_html, re.DOTALL)
                    if cell_match:
                        cell_html = cell_match.group(1)
                        cell_text = extract_text_from_cell(cell_html)

                        # Check if this cell is bold
                        is_bold = '<strong>' in cell_html

                        if is_bold:
                            # Sub-heading (abschnitt)
                            inhalte.append({
                                "abschnitt": cell_text
                            })
                        else:
                            # Regular content - add to inhalte
                            inhalte.append({
                                "text": cell_text
                            })
        else:
            # 3-col table: colspan bold rows + bold-only col1 rows = section headings
            tbody_match = re.search(r'<tbody>(.*?)</tbody>', table_html, re.DOTALL)
            if tbody_match:
                tbody_html = tbody_match.group(1)
                row_pattern = r'<tr[^>]*>(.*?)</tr>'

                current_section = None

                for row_match in re.finditer(row_pattern, tbody_html, re.DOTALL):
                    row_html = row_match.group(1)

                    # Check if colspan="3" (section heading)
                    if 'colspan="3"' in row_html:
                        cell_match = re.search(r'<td[^>]*colspan="3"[^>]*>(.*?)</td>', row_html, re.DOTALL)
                        if cell_match:
                            current_section = extract_text_from_cell(cell_match.group(1))
                            inhalte.append({
                                "abschnitt": current_section
                            })
                    else:
                        # Regular content row
                        cells = re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.DOTALL)
                        if len(cells) >= 2:
                            kognitive = extract_text_from_cell(cells[0]) if cells[0].strip() else None
                            handlung = extract_text_from_cell(cells[1]) if cells[1].strip() else None
                            richtzahl = None

                            if len(cells) > 2:
                                richtzahl_text = extract_text_from_cell(cells[2])
                                if richtzahl_text and richtzahl_text.isdigit():
                                    richtzahl = int(richtzahl_text)

                            # Only add if there's meaningful content
                            if kognitive or handlung:
                                inhalt_entry = {}
                                if current_section:
                                    inhalt_entry["abschnitt"] = current_section
                                if kognitive:
                                    inhalt_entry["kognitive_und_methodenkompetenz"] = kognitive
                                if handlung:
                                    inhalt_entry["handlungskompetenz"] = handlung
                                if richtzahl is not None:
                                    inhalt_entry["richtzahl"] = richtzahl

                                if inhalt_entry:
                                    inhalte.append(inhalt_entry)

    if inhalte:
        result["inhalte"] = inhalte

    return result

def main():
    """Process all 12 zusatz-weiterbildung HTML files."""
    gebiete_dir = Path("/home/tom/Projects/health_bot/gebiete")

    files_to_process = [
        "zusatz-weiterbildung-medikamentoese-tumortherapie.html",
        "zusatz-weiterbildung-medizinische-informatik.html",
        "zusatz-weiterbildung-naturheilverfahren.html",
        "zusatz-weiterbildung-notfallmedizin.html",
        "zusatz-weiterbildung-nuklearmedizinische-diagnostik-fuer-radiologen.html",
        "zusatz-weiterbildung-palliativmedizin.html",
        "zusatz-weiterbildung-phlebologie.html",
        "zusatz-weiterbildung-physikalische-therapie.html",
        "zusatz-weiterbildung-plastische-und-aesthetische-operationen.html",
        "zusatz-weiterbildung-proktologie.html",
        "zusatz-weiterbildung-psychoanalyse.html",
        "zusatz-weiterbildung-psychotherapie.html",
    ]

    for filename in files_to_process:
        filepath = gebiete_dir / filename
        json_filename = filename.replace('.html', '.json')
        json_filepath = gebiete_dir / json_filename

        print(f"Processing {filename}...", end=" ")
        try:
            data = parse_html_file(str(filepath))

            # Write JSON with proper formatting
            with open(json_filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            print(f"Created {json_filename}")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    main()
