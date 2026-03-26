#!/usr/bin/env python3
"""Convert MWBO HTML files to structured JSON."""

import json
import re
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple
import sys
from html.parser import HTMLParser


def normalize_text(text: str) -> str:
    """Normalize whitespace while preserving structure."""
    # Remove extra whitespace but preserve line breaks for readability
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def extract_text_from_html_element(html_str: str) -> str:
    """Extract plain text from HTML element, preserving basic structure."""
    # Remove tags but keep content
    text = re.sub(r'<[^>]+>', '', html_str)
    return normalize_text(text)


def parse_html_file(file_path: Path) -> Dict[str, Any]:
    """Parse an MWBO HTML file and convert to JSON."""

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    result = {
        'typ': 'facharzt',
        'gebiet': '',
        'bezeichnung': '',
    }

    # Extract H3 (Gebiet)
    h3_match = re.search(r'<h3[^>]*>([^<]+(?:<[^>]*>[^<]*)*)</h3>', content)
    if h3_match:
        h3_text = extract_text_from_html_element(h3_match.group(1))
        # Remove "Gebiet " prefix
        if h3_text.startswith('Gebiet '):
            result['gebiet'] = h3_text[7:]
        else:
            result['gebiet'] = h3_text

    # Extract H4 (designation)
    h4_match = re.search(r'<h4[^>]*>([^<]+(?:<[^>]*>[^<]*)*)</h4>', content)
    if h4_match:
        h4_text = extract_text_from_html_element(h4_match.group(1))
        result['bezeichnung'] = h4_text

        # Determine type
        if h4_text.startswith('Zusatz-Weiterbildung'):
            result['typ'] = 'zusatz-weiterbildung'
        elif h4_text.startswith('Schwerpunkt'):
            result['typ'] = 'schwerpunkt'
        else:
            result['typ'] = 'facharzt'

    # Extract zusatzbezeichnung from blockquote after H4 (look for pattern: (Something))
    blockquote_match = re.search(
        r'</h4>\s*<blockquote>\s*<p>\(([^)]+)\)</p>\s*</blockquote>',
        content
    )
    if blockquote_match:
        result['zusatzbezeichnung'] = blockquote_match.group(1)

    # Parse first table (definition and basic info)
    first_table_match = re.search(
        r'<table[^>]*>.*?<thead>.*?</thead>.*?<tbody>(.*?)</tbody>.*?</table>',
        content,
        re.DOTALL
    )

    if first_table_match:
        tbody = first_table_match.group(1)
        # Find all rows
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', tbody, re.DOTALL)

        for row in rows:
            cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)

            if len(cells) >= 2:
                label_cell = cells[0]
                content_cell = cells[1]

                # Extract label from first cell
                label_match = re.search(r'<strong>([^<]+)</strong>', label_cell)
                if label_match:
                    label = label_match.group(1).strip()
                    content_text = extract_text_from_html_element(content_cell)

                    if 'Gebietsdefinition' in label and 'gebietsdefinition' not in result:
                        result['gebietsdefinition'] = content_text
                    elif label == 'Definition' and 'definition' not in result:
                        result['definition'] = content_text
                    elif 'Voraussetzung' in label and 'voraussetzung' not in result:
                        result['voraussetzung'] = content_text
                    elif 'Mindestanforderungen' in label and 'mindestanforderungen' not in result:
                        result['mindestanforderungen'] = content_text
                    elif 'Weiterbildungszeit' in label and 'weiterbildungszeit' not in result:
                        result['weiterbildungszeit'] = content_text

    # Parse content tables
    inhalte = []

    # Find all tables after the first one
    tables = re.findall(
        r'<table[^>]*>.*?<colgroup>.*?</colgroup>.*?(?:<thead>.*?</thead>)?.*?<tbody>(.*?)</tbody>.*?</table>',
        content,
        re.DOTALL
    )

    # Skip the first table (already processed)
    tables = tables[1:] if tables else []

    for table_body in tables:
        rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_body, re.DOTALL)

        for row in rows:
            # Check for colspan attribute to identify section headers
            colspan_match = re.search(r'colspan="3"', row)

            if colspan_match:
                # This is a section header
                # Extract text from the colspan cell
                td_match = re.search(r'<td[^>]*colspan="3"[^>]*>(.*?)</td>', row, re.DOTALL)
                if td_match:
                    text = extract_text_from_html_element(td_match.group(1))
                    if text:
                        inhalte.append({'abschnitt': text})
            else:
                # Regular content row - extract cells
                cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)

                if len(cells) == 3:
                    # 3-column table: kognitive, handlung, richtzahl
                    kognitive = extract_text_from_html_element(cells[0])
                    handlung = extract_text_from_html_element(cells[1])
                    richtzahl_text = extract_text_from_html_element(cells[2])

                    # Only add if there's meaningful content
                    if kognitive or handlung or richtzahl_text:
                        entry: Dict[str, Any] = {'abschnitt': 'content'}

                        if kognitive:
                            entry['kognitive_und_methodenkompetenz'] = kognitive
                        if handlung:
                            entry['handlungskompetenz'] = handlung

                        # Extract numeric richtzahl
                        if richtzahl_text:
                            num_match = re.search(r'\d+', richtzahl_text)
                            if num_match:
                                entry['richtzahl'] = int(num_match.group(0))

                        inhalte.append(entry)

                elif len(cells) == 1:
                    # 1-column table - check if it's bold (section header) or regular text
                    cell_content = cells[0]
                    text = extract_text_from_html_element(cell_content)

                    if text:
                        # Check if contains <strong> for section headers
                        if '<strong>' in cell_content:
                            inhalte.append({'abschnitt': text})
                        else:
                            # Regular content
                            inhalte.append({
                                'abschnitt': 'content',
                                'text': text
                            })

    if inhalte:
        result['inhalte'] = inhalte

    # Remove empty string values and None
    result = {k: v for k, v in result.items() if v is not None and v != ''}

    return result


def process_file(file_path: Path, output_dir: Path) -> bool:
    """Process a single file and write JSON output."""
    try:
        print(f"Processing: {file_path.name}")
        data = parse_html_file(file_path)

        # Generate output filename
        output_file = output_dir / file_path.name.replace('.html', '.json')

        # Write JSON with nice formatting
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"  -> {output_file.name}")
        return True
    except Exception as e:
        print(f"ERROR processing {file_path.name}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main entry point."""
    base_dir = Path('/home/tom/Projects/health_bot/gebiete')

    files_to_process = [
        'gebiet-physiologie.html',
        'gebiet-psychiatrie-und-psychotherapie-fuer-psychiatrie-und-psychotherapie.html',
        'gebiet-psychiatrie-und-psychotherapie-schwerpunkt-forensische-psychiatrie.html',
        'gebiet-psychosomatische-medizin-und-psychotherapie.html',
        'gebiet-radiologie-radiologie.html',
        'gebiet-radiologie-schwerpunkt-kinder-und-jugendradiologie.html',
        'gebiet-radiologie-schwerpunkt-neuroradiologie.html',
        'gebiet-rechtsmedizin.html',
        'gebiet-strahlentherapie.html',
        'gebiet-transfusionsmedizin.html',
        'gebiet-urologie.html',
    ]

    success_count = 0
    for filename in files_to_process:
        file_path = base_dir / filename
        if file_path.exists():
            if process_file(file_path, base_dir):
                success_count += 1
        else:
            print(f"File not found: {file_path}")

    print(f"\nCompleted: {success_count}/{len(files_to_process)} files processed successfully")


if __name__ == '__main__':
    main()
