"""Build dated section-retrieval cases from the public eCFR revision history."""

import argparse
from collections import defaultdict
from datetime import date, timedelta
import json
from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET

import requests


BASE = 'https://www.ecfr.gov/api/versioner/v1'


def fetch(url, path):
    if not path.exists():
        time.sleep(2)
        session = requests.Session()
        session.trust_env = False
        response = session.get(url, timeout=90)
        response.raise_for_status()
        path.write_bytes(response.content)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--title', type=int, default=21)
    parser.add_argument('--part', default='117')
    parser.add_argument('--start', type=date.fromisoformat, default=date(2017, 1, 1))
    parser.add_argument('--end', type=date.fromisoformat, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    source = args.output / 'sources'
    source.mkdir(exist_ok=True)
    versions_url = f'{BASE}/versions/title-{args.title}.json?part={args.part}'
    rows = json.loads(fetch(versions_url, source / 'versions.json').read_text())['content_versions']
    groups = defaultdict(list)
    for row in rows:
        if row['type'] == 'section' and row['date'] <= args.end.isoformat():
            groups[row['identifier']].append(row)
    dates = sorted({args.start.isoformat()} | {row['date'] for row in rows
                   if args.start.isoformat() <= row['date'] <= args.end.isoformat()})
    urls = {day: f'{BASE}/full/{day}/title-{args.title}.xml?part={args.part}' for day in dates}
    for day in dates:
        fetch(urls[day], source / f'{day}.xml')
    snapshots = {}
    for day in dates:
        tree = ET.parse(source / f'{day}.xml')
        snapshots[day] = {node.attrib['N']: re.sub(r'\s+', ' ', ''.join(node.itertext())).strip()
                          for node in tree.iter() if node.attrib.get('TYPE') == 'SECTION'}
    documents, queries = [], []
    for section, history in sorted(groups.items()):
        events = defaultdict(list)
        for event in history:
            events[event['date']].append(event)
        history = [min(group, key=lambda row: row['issue_date']) for _, group in sorted(events.items())]
        available = [r for r in history if r['date'] <= args.start.isoformat()]
        timeline = ([available[-1]] if available else []) + [r for r in history if r['date'] > args.start.isoformat()]
        for index, row in enumerate(timeline):
            begin = max(row['date'], args.start.isoformat())
            finish = timeline[index + 1]['date'] if index + 1 < len(timeline) else None
            document_id = f'{section}@{row["date"]}'
            if row['removed']:
                continue
            text = snapshots[begin][section]
            documents.append(dict(id=document_id, section=section, title=row['name'], text=text,
                                  valid_from=begin, valid_until=finish, revision_date=row['date'],
                                  amendment_date=row['amendment_date'], issue_date=row['issue_date'],
                                  substantive=row['substantive'], version_events=events[row['date']], source_url=urls[begin]))
            if len(timeline) < 2:
                continue
            end = date.fromisoformat(finish) - timedelta(days=1) if finish else args.end
            for day in sorted({begin, end.isoformat()}):
                if day < begin:
                    continue
                query_id = f'{section}@{day}'
                queries.append(dict(id=query_id, section=section, as_of=day,
                                    query=f'Find {args.title} CFR {section}, {row["name"]}, as shown in the eCFR on {day}.',
                                    expected_id=document_id))
    (args.output / 'documents.json').write_text(json.dumps(documents, indent=2) + '\n')
    (args.output / 'queries.json').write_text(json.dumps(queries, indent=2) + '\n')
    summary = dict(title=args.title, part=args.part, start=args.start.isoformat(), end=args.end.isoformat(),
                   sections=len(groups), documents=len(documents), queries=len(queries),
                   version_history=versions_url, snapshot_urls=urls,
                   date_semantics='eCFR displayed revision date',
                   query_construction='First and last covered calendar day of each version interval for sections with multiple revisions.')
    (args.output / 'corpus.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
