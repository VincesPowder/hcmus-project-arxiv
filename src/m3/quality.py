"""Validate positive matching pairs and evaluate on human-labelled pairs."""
import csv
from .labelling import FIELDS, prepare, match


def read_labels(path):
    labels = {}
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != FIELDS:
            raise ValueError(f'{path}: expected columns {FIELDS}')
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f'{path}: malformed CSV row')
            key = row['arxiv_id'], row['citation_key']
            if key in labels:
                raise ValueError(f'{path}: duplicate label {key}')
            labels[key] = row
    return labels


def validate(store, chosen, labels, source):
    errors, papers, owners = [], [], {}
    allowed = {r['arxiv_id'] for r in chosen}
    by_paper = {}
    for (pid, key), row in labels.items():
        by_paper.setdefault(pid, set()).add(key)
        if pid not in allowed:
            errors.append(f'{pid}/{key}: outside selected {source} papers')
        if row['label_source'] != source:
            errors.append(f'{pid}/{key}: invalid label_source')
    for paper in chosen:
        pid = paper['arxiv_id']
        entries = store.paper('bib', pid)
        refs = {r['ref_arxiv_id'] for r in store.paper('ref', pid)}
        keys = {r['citation_key'] for r in entries}
        actual = by_paper.get(pid, set())
        if not keys or not refs:
            errors.append(f'{pid}: missing source data')
        if actual - keys:
            errors.append(f'{pid}: {len(actual-keys)} unknown entry labels')
        count = 0
        for key in actual:
            target = labels[pid, key]['groundtruth_arxiv_id']
            if not target:
                errors.append(f'{pid}/{key}: empty groundtruth is not a matching label; remove legacy blank rows')
            elif target not in refs:
                errors.append(f'{pid}/{key}: reference outside this paper')
            elif target and key in keys:
                count += 1
        if source == 'manual' and count < 20:
            errors.append(f'{pid}: manual pairs {count} < 20')
        if source == 'auto' and count == 0:
            errors.append(f'{pid}: no accepted automatic matching pairs')
        papers.append(dict(arxiv_id=pid, owner=paper['owner'], partition=paper['partition'], entries=len(keys), matched=count, unlabelled=len(keys-actual)))
        stats = owners.setdefault(paper['owner'], dict(papers=0, labelled_papers=0, matched=0))
        stats['papers'] += 1
        stats['labelled_papers'] += count > 0
        stats['matched'] += count
    for owner, stats in owners.items():
        required = 5 if source == 'manual' else 500
        if stats['papers'] != required:
            errors.append(f'{owner}: {source} paper count {stats["papers"]} != {required}')
        if source == 'manual' and stats['matched'] < 100:
            errors.append(f'{owner}: manual pairs {stats["matched"]} < 100')
    if not chosen:
        errors.append(f'No selected {source} papers')
    return dict(valid=not errors, errors=errors, papers=papers, owners=owners)


def evaluate(store, chosen, human, threshold=.94, margin=.04):
    """Compare fixed matcher to positive human pairs only.

    Precision/coverage are conditional on this labelled subset. Missing entry
    labels are unknown, never negatives. This diagnostic does not generate auto
    labels for manual papers and must not be merged into training labels.
    """
    groups, details = {}, []
    for paper in chosen:
        pid = paper['arxiv_id']
        references = [(r, prepare(r, True)) for r in store.paper('ref', pid)]
        for entry in store.paper('bib', pid):
            key = (pid, entry['citation_key'])
            if key not in human:
                continue
            truth = human[key]['groundtruth_arxiv_id']
            if not truth:
                raise ValueError('Manual evaluation requires positive matching pairs; remove blank rows')
            predicted, value, reason = match(entry, references, threshold, margin)
            correct = bool(predicted and predicted == truth)
            for group in ('all', 'owner:' + paper['owner'], 'partition:' + paper['partition'], 'owner:' + paper['owner'] + '/partition:' + paper['partition']):
                counts = groups.setdefault(group, dict(reviewed=0, positives=0, predicted=0, correct=0, exact=0))
                counts['reviewed'] += 1
                counts['positives'] += bool(truth)
                counts['predicted'] += bool(predicted)
                counts['correct'] += correct
                counts['exact'] += predicted == truth
            details.append(dict(arxiv_id=pid, citation_key=entry['citation_key'], truth=truth,
                                predicted=predicted, score=value, reason=reason))
    for counts in groups.values():
        counts['precision'] = counts['correct'] / counts['predicted'] if counts['predicted'] else None
        counts['recall'] = counts['correct'] / counts['positives'] if counts['positives'] else None
        counts['coverage'] = counts['predicted'] / counts['reviewed'] if counts['reviewed'] else None
        counts['accuracy'] = counts['exact'] / counts['reviewed'] if counts['reviewed'] else None
    return groups, details
