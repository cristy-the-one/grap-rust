"""Recover explicit boxed table grids without guessing flattened columns."""
import csv
import io
import re

_BOX = re.compile(r"┌(?P<top>[─┬]+)┐(?P<body>.*?)└[─┴]+┘", re.S)
_RULES = set(" \t\r\n─━═├┤┼┬┴")


def extract_tables(text):
    tables = []
    for box in _BOX.finditer(text):
        width = box['top'].count('┬') + 1
        if width < 2:
            continue
        pattern = re.compile('│' + '([^│]*)│' * width)
        rows, cursor, valid = [], box.start('body'), True
        for match in pattern.finditer(text, box.start('body'), box.end('body')):
            if any(c not in _RULES for c in text[cursor:match.start()]):
                valid = False
                break
            cells = []
            for column in range(1, width + 1):
                value = match[column]
                start = match.start(column) + len(value) - len(value.lstrip())
                value = value.strip()
                cells.append({'column': column, 'text': value, 'start': start, 'end': start + len(value)})
            rows.append(cells)
            cursor = match.end()
        if not valid or len(rows) < 2 or any(c not in _RULES for c in text[cursor:box.end('body')]):
            continue
        stream = io.StringIO(newline='')
        csv.writer(stream, delimiter='\t', lineterminator='\n').writerows(
            [[cell['text'] for cell in row] for row in rows])
        tables.append({'start': box.start(), 'end': box.end(), 'columns': width,
                       'rows': rows, 'tsv': stream.getvalue(),
                       'interpretation': 'physical grid rows; wrapped cells, headers and units are not inferred'})
    return tables
