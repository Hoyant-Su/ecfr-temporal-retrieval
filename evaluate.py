"""Compare lexical retrieval, citation lookup, and revision-aware retrieval."""

import argparse
from collections import Counter
import csv
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from build import BASE, fetch


def tokens(text):
    return re.findall(r'[a-z]+|\d+(?:\.\d+)?', text.lower())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    documents = json.loads((args.data / 'documents.json').read_text())
    queries = json.loads((args.data / 'queries.json').read_text())
    corpus = json.loads((args.data / 'corpus.json').read_text())
    by_id = {doc['id']: doc for doc in documents}
    counts = {doc['id']: Counter(tokens(doc['title'] + ' ' + doc['text'])) for doc in documents}
    lengths = {key: sum(value.values()) for key, value in counts.items()}
    mean_length = sum(lengths.values()) / len(lengths)
    frequency = Counter(term for count in counts.values() for term in count)

    def score(query, document):
        count = counts[document['id']]
        return sum(math.log(1 + (len(documents) - frequency[term] + 0.5) / (frequency[term] + 0.5))
                   * count[term] * 2.5 / (count[term] + 1.5 * (0.25 + 0.75 * lengths[document['id']] / mean_length))
                   for term in set(tokens(query)) if count[term])

    references = {}
    for day in sorted({query['as_of'] for query in queries}):
        url = f'{BASE}/full/{day}/title-{corpus["title"]}.xml?part={corpus["part"]}'
        path = fetch(url, args.data / 'sources' / f'{day}.xml')
        references[day] = {node.attrib['N']: re.sub(r'\s+', ' ', ''.join(node.itertext())).strip()
                           for node in ET.parse(path).iter() if node.attrib.get('TYPE') == 'SECTION'}
    records = []
    for query in queries:
        expected = by_id[query['expected_id']]
        assert expected['text'] == references[query['as_of']][query['section']], query['id']
        active = [doc for doc in documents if doc['valid_from'] <= query['as_of']
                  and (doc['valid_until'] is None or query['as_of'] < doc['valid_until'])]
        chosen = {
            'bm25': max(documents, key=lambda doc: (score(query['query'], doc), doc['revision_date'], doc['id'])),
            'citation_latest': max((doc for doc in documents if doc['section'] == query['section']),
                                   key=lambda doc: doc['revision_date']),
            'bm25_as_of': max(active, key=lambda doc: (score(query['query'], doc), doc['revision_date'], doc['id'])),
            'citation_as_of': next(doc for doc in active if doc['section'] == query['section']),
        }
        for method, observed in chosen.items():
            records.append(dict(query_id=query['id'], section=query['section'], as_of=query['as_of'],
                                method=method, expected_id=expected['id'], retrieved_id=observed['id'],
                                correct_section=observed['section'] == query['section'],
                                correct_revision=observed['id'] == expected['id'],
                                correct_text=observed['text'] == expected['text']))
    with (args.output / 'retrievals.csv').open('w') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)
    summary = []
    for method in chosen:
        rows = [r for r in records if r['method'] == method]
        summary.append(dict(method=method, queries=len(rows),
                            **{key: sum(r[key] for r in rows) / len(rows)
                               for key in ['correct_section', 'correct_revision', 'correct_text']}))
    (args.output / 'summary.json').write_text(json.dumps(dict(
        source_snapshot_checks=len(queries), bm25=dict(k1=1.5, b=0.75),
        tie_order=['score', 'revision_date', 'document_id'], methods=summary), indent=2) + '\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
