"""Validate labels, evaluate the fixed matcher on manual papers, and package labels."""
import argparse
import hashlib
import json
from pathlib import Path
from m3.labelling import arguments, open_store, selection, write_csv
from m3.quality import read_labels, validate, evaluate


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    arguments(parser)
    parser.add_argument('--manual', required=True)
    parser.add_argument('--auto', help='Optional auto CSV to validate and merge with manual')
    parser.add_argument('--threshold', type=float, default=.94)
    parser.add_argument('--margin', type=float, default=.04)
    parser.add_argument('--meta', help='selection_meta.json; requires --candidates')
    parser.add_argument('--candidates', help='Only read/hash candidates; never run split.py')
    args = parser.parse_args()
    if not 0 <= args.threshold <= 1 or not 0 <= args.margin <= 1:
        parser.error('threshold and margin must be in [0,1]')
    if bool(args.meta) != bool(args.candidates):
        parser.error('--meta and --candidates must be supplied together')
    store = open_store(args)
    manual = read_labels(args.manual)
    chosen = selection(args.selection, args.owner, 'manual')
    validation = validate(store, chosen, manual, 'manual')
    report = dict(manual=validation, threshold=args.threshold, margin=args.margin,
                  sha256={'selection': digest(args.selection), 'manual': digest(args.manual)},
                  metrics=None, notes=['Fixed matcher; do not tune on test.', 'Blank auto labels are abstentions, not verified negatives.'])
    if args.meta:
        meta = json.loads(Path(args.meta).read_text(encoding='utf-8'))
        actual = digest(args.candidates)
        report['candidates_hash_matches'] = actual == meta['candidates_sha256']
        report['sha256'].update(candidates=actual, meta=digest(args.meta))
        if not report['candidates_hash_matches']:
            validation['errors'].append('Candidates SHA-256 differs from selection_meta.json')
            validation['valid'] = False
    report['cache_manifest'] = dict(store.db.execute('SELECT kind,fingerprint FROM manifest'))
    report['sha256']['matcher_code'] = digest(Path(__file__).parent / 'm3' / 'labelling.py')
    merged = dict(manual)
    if args.auto:
        auto = read_labels(args.auto)
        report['auto'] = validate(store, selection(args.selection, args.owner, 'auto'), auto, 'auto')
        report['sha256']['auto'] = digest(args.auto)
        if manual.keys() & auto.keys():
            report['auto']['errors'].append('Manual/auto overlap')
            report['auto']['valid'] = False
        merged.update(auto)
    valid = validation['valid'] and (not args.auto or report['auto']['valid'])
    if validation['valid']:
        report['metrics'], details = evaluate(store, chosen, manual, args.threshold, args.margin)
        write_csv(args.output + '.matcher.csv', details, ['arxiv_id','citation_key','truth','predicted','score','reason'])
        metric_fields = ['group','reviewed','positives','predicted','correct','exact','precision','recall','coverage','accuracy']
        write_csv(args.output + '.metrics.csv', [dict(group=g, **v) for g,v in report['metrics'].items()], metric_fields)
    report['valid'] = valid
    report['merged_written'] = bool(valid and args.auto)
    destination = Path(args.output + '.report.json')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(destination)
    if valid and args.auto:
        write_csv(args.output, (merged[k] for k in sorted(merged)))
    store.db.close()
    print(f'Validation: {"PASS" if valid else "FAIL"}; report: {destination}')
    if not valid:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
