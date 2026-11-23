import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from m3.labelling import ids, match, prepare, Store
import tempfile
import csv


class LabellingTests(unittest.TestCase):
    def test_ids_and_conflicts(self):
        self.assertEqual(ids('https://arxiv.org/abs/2606.01234v2'), {'2606.01234'})
        r = {'ref_arxiv_id': '2606.01234', 'ref_title': 'A long title about language models'}
        refs = [(r, prepare(r, True))]
        self.assertEqual(match({'url': 'arxiv:2606.01234v2'}, refs)[0], '2606.01234')
        self.assertEqual(match({'url': 'arxiv:2606.99999', 'title': r['ref_title']}, refs)[0], '')
        self.assertEqual(match({'url': '2606.01234 and 2606.99999'}, refs)[0], '')

    def test_metadata_and_ties(self):
        entry = dict(title='Learning reliable representations for scientific documents', authors=['Jane Smith'], year=2025)
        r = dict(ref_arxiv_id='2501.00001', ref_title=entry['title'], ref_authors=['J. Smith'], ref_submission_date='2025-01-01')
        refs = [(r, prepare(r, True))]
        self.assertEqual(match(entry, refs)[0], r['ref_arxiv_id'])
        second = {**r, 'ref_arxiv_id': '2501.00002'}
        self.assertEqual(match(entry, refs + [(second, prepare(second, True))])[0], '')
        self.assertEqual(match({'title': 'Introduction'}, refs)[0], '')

    def test_disk_index_twenty_thousand_papers(self):
        # Generated CSV is an input-adapter/load test, not a real-corpus benchmark.
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as tmp:
            path = Path(tmp) / 'bib.csv'
            with path.open('w', newline='') as f:
                w = csv.DictWriter(f, fieldnames=['arxiv_id','citation_key','title'])
                w.writeheader()
                for n in range(20000):
                    w.writerow(dict(arxiv_id=f'2606.{n:05}', citation_key='key', title='Example'))
            store = Store(Path(tmp) / 'cache.sqlite')
            store.load('bib', [str(path)])
            store.load('bib', [str(path)])
            self.assertEqual(store.db.execute('SELECT count(*) FROM rows').fetchone()[0], 20000)
            self.assertEqual(len(store.paper('bib','2606.01999')), 1)
            store.db.close()


if __name__ == '__main__':
    unittest.main()
