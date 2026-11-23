"""Conservative automatic labels, one paper at a time; never changes selection."""
import argparse
import time
from m3.labelling import arguments, selection, open_store, prepare, match, write_csv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    parser.add_argument('--threshold', type=float, default=.94)
    parser.add_argument('--margin', type=float, default=.04)
    parser.add_argument('--all-papers', action='store_true', help='Diagnostic mode: all indexed papers; excludes every manual/reserve/dropped paper')
    args = parser.parse_args()
    if not 0 <= args.threshold <= 1 or not 0 <= args.margin <= 1:
        parser.error('threshold and margin must be in [0,1]')
    started = time.monotonic()
    chosen = selection(args.selection, args.owner)
    if not chosen and not args.all_papers:
        parser.error('No auto papers selected; check owner and selection')
    store = open_store(args)
    if args.all_papers:
        import csv
        with open(args.selection, newline='', encoding='utf-8-sig') as f:
            excluded = {r['arxiv_id'] for r in csv.DictReader(f) if r['kind'] != 'auto'}
        if args.owner:
            parser.error('--all-papers cannot be combined with --owner; use normal selection mode')
        chosen = [{'arxiv_id': r[0]} for r in store.db.execute("SELECT DISTINCT paper FROM rows WHERE kind='bib' ORDER BY paper") if r[0] not in excluded]
    import csv
    from pathlib import Path
    from m3.labelling import FIELDS
    audit = Path(args.output + '.audit.csv')
    audit.parent.mkdir(parents=True, exist_ok=True)
    temporary = audit.with_suffix('.csv.tmp')
    counts = [0, 0]
    paper_reports = []
    def generate(writer):
        for index, paper in enumerate(chosen, 1):
            pid = paper['arxiv_id']
            entries, refs = store.paper('bib', pid), store.paper('ref', pid)
            if not entries or not refs:
                raise ValueError(f'{pid}: missing bibtex/references; check input ranges')
            refs = [(r, prepare(r, True)) for r in refs]
            accepted = 0
            for entry in entries:
                target, value, reason = match(entry, refs, args.threshold, args.margin)
                label = dict(arxiv_id=pid, citation_key=entry['citation_key'], groundtruth_arxiv_id=target, label_source='auto')
                writer.writerow({**label, 'score': value, 'reason': reason})
                counts[0] += bool(target)
                counts[1] += 1
                if target:
                    accepted += 1
                    yield label
            paper_reports.append(dict(arxiv_id=pid, entries=len(entries), matched=accepted, abstained=len(entries)-accepted, status='labelled' if accepted else 'no_matches'))
            if index % 100 == 0:
                print(f'{index}/{len(chosen)} papers, {time.monotonic()-started:.1f}s', flush=True)
    with temporary.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS + ['score', 'reason'])
        writer.writeheader()
        write_csv(args.output, generate(writer))
    temporary.replace(audit)
    write_csv(args.output + '.papers.csv', paper_reports, ['arxiv_id','entries','matched','abstained','status'])
    store.db.close()
    print(f'{len(chosen)} processed papers; {sum(r["matched"] > 0 for r in paper_reports)} labelled papers; {counts[0]} matched / {counts[1]} entries')
    if any(not r['matched'] for r in paper_reports):
        raise SystemExit('Incomplete automatic labelling: papers with zero pairs; inspect .papers.csv and audit. Selection was not changed.')



if __name__ == '__main__':
    main()
