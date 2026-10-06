"""Conservative explicit table extraction; values remain unverified source quotes."""
import hashlib
import json
import re

PARSER_VERSION = "structured-tables/v1"
SCHEMAS = {
    "troubleshooting": {"symptom": ("fault", "symptom", "problem", "trouble"), "cause": ("cause", "causes", "probable cause", "possible cause"), "remedy": ("remedy", "remedies", "corrective action", "action")},
    "parts": {"part_number": ("part number", "part no", "article no", "article number"), "description": ("description", "designation", "part description"), "quantity": ("quantity", "qty"), "position": ("position", "pos", "item")},
    "alarm": {"code": ("alarm code", "fault code", "code"), "meaning": ("meaning", "alarm description", "description"), "condition": ("condition", "trigger condition"), "action": ("action", "corrective action", "remedy")},
    "specification": {"parameter": ("parameter", "specification", "characteristic"), "value": ("value", "rating", "rated value"), "unit": ("unit", "units"), "minimum": ("minimum", "min"), "maximum": ("maximum", "max")},
}
REQUIRED = {"troubleshooting": {"symptom", "cause", "remedy"}, "parts": {"part_number", "description"}, "alarm": {"code", "meaning"}, "specification": {"parameter", "value"}}

def structured_hash(records):
    return hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

def cells(line):
    value = line.strip()
    if value.startswith("|"): value = value[1:]
    if value.endswith("|") and not value.endswith("\\|"): value = value[:-1]
    return [part.strip().replace("\\|", "|") for part in re.split(r"(?<!\\)\|", value)]

def parse_tables(text):
    records, issues = [], []
    active = None
    for number, line in enumerate(text.splitlines(), 1):
        if "|" not in line:
            active = None
            continue
        values = cells(line)
        if all(re.fullmatch(r"[-: ]*", v) for v in values): continue
        headers = [re.sub(r"\s+", " ", v.lower().strip().rstrip(".")) for v in values]
        matches = []
        ambiguous = False
        for kind, schema in SCHEMAS.items():
            roles = {}
            duplicate = False
            for index, header in enumerate(headers):
                for role, labels in schema.items():
                    if header in labels:
                        duplicate |= role in roles
                        roles[role] = index
            if REQUIRED[kind] <= roles.keys():
                if duplicate: ambiguous = True
                else: matches.append((kind, roles))
        if ambiguous or len(matches) > 1:
            issues.append({"line": number, "source_quote": line, "reason": "ambiguous_headers"})
            active = None
            continue
        if matches:
            active = (*matches[0], len(values), line, number)
            continue
        if active is None: continue
        kind, roles, width, header_quote, header_line = active
        if len(values) != width or any(not values[roles[role]] for role in REQUIRED[kind]):
            issues.append({"line": number, "source_quote": line, "reason": "incomplete_or_malformed_row"})
            continue
        records.append({"kind": kind, "fields": {role: values[index] for role, index in roles.items()}, "source_quote": line, "source_line": number, "header_quote": header_quote, "header_line": header_line, "field_columns": {role: index + 1 for role, index in roles.items()}, "validation_status": "extracted", "parser_version": PARSER_VERSION})
    return records, issues

def parse_labeled_text(text):
    """Only explicit labels establish fault/cause/remedy relationships."""
    records, issues = [], []
    fields, quotes, lines = {}, [], []
    labels = {'fault':'symptom', 'symptom':'symptom', 'problem':'symptom', 'cause':'cause', 'possible cause':'cause', 'probable cause':'cause', 'remedy':'remedy', 'corrective action':'remedy'}
    def flush():
        if not fields: return
        if set(fields) == {'symptom', 'cause', 'remedy'}:
            records.append({'kind':'troubleshooting', 'fields':dict(fields), 'source_quote':'\n'.join(quotes), 'source_lines':list(lines), 'validation_status':'extracted', 'parser_version':PARSER_VERSION})
        else:
            issues.append({'source_quote':'\n'.join(quotes), 'source_lines':list(lines), 'reason':'incomplete_labeled_group'})
        fields.clear(); quotes.clear(); lines.clear()
    for number, line in enumerate(text.splitlines(), 1):
        match = re.fullmatch(r'\s*([A-Za-z ]+):\s*(\S.*)', line) if '|' not in line else None
        role = labels.get(match[1].strip().lower()) if match else None
        if not role:
            flush()
            continue
        if role in fields: flush()
        fields[role] = match[2].strip(); quotes.append(line); lines.append(number)
    flush()
    return records, issues

def parse_source(text):
    tables, table_issues = parse_tables(text)
    labeled, labeled_issues = parse_labeled_text(text)
    return tables + labeled, table_issues + labeled_issues
