"""Terminal reference-by-reference human labelling with atomic resume saves."""
import argparse
import csv
from pathlib import Path
from m3.labelling import arguments, selection, open_store, prepare, score, write_csv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    parser.add_argument('--top', type=int, default=10, choices=range(5, 11))
    args = parser.parse_args()
    chosen = selection(args.selection, args.owner, 'manual')
    if not chosen:
        parser.error('No manual papers selected; check owner and selection')
    chosen.sort(key=lambda r: (float(r.get('n_any_hit') or 0) > 20, r['partition'] != 'test', float(r.get('n_any_hit') or 0), r['arxiv_id']))
    allowed = {r['arxiv_id'] for r in chosen}
    store = open_store(args)
    labels = {}
    if Path(args.output).exists():
        with open(args.output, newline='', encoding='utf-8-sig') as f:
            for row in csv.DictReader(f):
                if row['arxiv_id'] not in allowed or row['label_source'] != 'manual':
                    raise ValueError('Output belongs to a different selection/owner; choose a separate output file')
                if not row['groundtruth_arxiv_id']:
                    raise ValueError('Legacy blank manual rows: remove them from a copy before resuming; only matching pairs are labels')
                key = row['arxiv_id'], row['citation_key']
                if key in labels:
                    raise ValueError(f'Duplicate label: {key}')
                labels[key] = row
    def save():
        write_csv(args.output, [labels[k] for k in sorted(labels)])
    try:
        for paper in chosen:
            pid = paper['arxiv_id']
            entries, refs = store.paper('bib', pid), store.paper('ref', pid)
            if not entries or not refs:
                raise ValueError(f'{pid}: missing bibtex/references')
            by_key = {e['citation_key']: e for e in entries}
            targets = {r['ref_arxiv_id'] for r in refs}
            for (p, key), label in labels.items():
                if p == pid and (key not in by_key or label['groundtruth_arxiv_id'] not in targets | {''}):
                    raise ValueError(f'Stale label: {p}/{key}')
            prepared_entries = [(e, prepare(e)) for e in entries]
            for ref in refs:
                print(f'\n{pid} [{paper["partition"]}] REFERENCE {ref["ref_arxiv_id"]}\n{ref.get("ref_title")}\n{ref.get("ref_authors")} {ref.get("ref_submission_date")}')
                prepared_ref = prepare(ref, True)
                ranked = sorted([(score(prepared, prepared_ref)[0], e) for e, prepared in prepared_entries], key=lambda x: (-x[0], x[1]['citation_key']))
                shown = ranked[:args.top]
                for i, (value, entry) in enumerate(shown, 1):
                    previous = labels.get((pid, entry['citation_key']), {}).get('groundtruth_arxiv_id', '')
                    print(f'{i}. {entry["citation_key"]} [{value:.3f}] {entry.get("title")} | {entry.get("authors")} | {entry.get("year")} [assigned={previous}]')
                while True:
                    command = input('Numbers (comma separated), key:KEY, clear:KEY, all, skip, or quit: ').strip()
                    if command == 'quit':
                        save()
                        return
                    if command == 'skip':
                        break
                    if command.startswith('clear:'):
                        key = command[6:]
                        if key not in by_key:
                            print('Unknown citation key.')
                            continue
                        labels.pop((pid, key), None)
                        save()
                        print('Label removed; this entry is now unlabelled.')
                        continue
                    if command == 'all':
                        for entry in entries:
                            print(entry)
                        continue
                    keys = [command[4:]] if command.startswith('key:') else []
                    if not keys:
                        try:
                            numbers = [int(n.strip()) for n in command.split(',')]
                            if any(n < 1 or n > len(shown) for n in numbers):
                                raise ValueError
                            keys = [shown[n-1][1]['citation_key'] for n in numbers]
                        except ValueError:
                            print('Invalid choice.')
                            continue
                    if any(k not in by_key for k in keys):
                        print('Unknown citation key.')
                        continue
                    conflicts = [k for k in keys if labels.get((pid,k), {}).get('groundtruth_arxiv_id') not in (None, '', ref['ref_arxiv_id'])]
                    if conflicts and input(f'WARNING: {conflicts} already assigned to another reference. Type REASSIGN: ') != 'REASSIGN':
                        continue
                    for key in keys:
                        labels[pid,key] = dict(arxiv_id=pid, citation_key=key, groundtruth_arxiv_id=ref['ref_arxiv_id'], label_source='manual')
                    save()
                    break
            save()  # Unselected entries remain unknown, not negative labels.
            count = sum(bool(v['groundtruth_arxiv_id']) for (p,k), v in labels.items() if p == pid)
            print(f'{pid}: {count} pairs. ' + ('OK' if count >= 20 else 'BELOW 20: review or replace with same-owner/same-tier reserve using split.py --replace.'))
    except (KeyboardInterrupt, EOFError):
        save()
    for owner in sorted({r['owner'] for r in chosen}):
        papers = {r['arxiv_id'] for r in chosen if r['owner'] == owner}
        count = sum(bool(v['groundtruth_arxiv_id']) for (p,k),v in labels.items() if p in papers)
        print(f'{owner}: {count} pairs (required >=100 across 5 papers, each >=20).')


if __name__ == '__main__':
    main()
