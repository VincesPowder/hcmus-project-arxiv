"""End-to-end checks with synthetic data; no Drive or network access."""
import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from m3.labelling import FIELDS, write_csv
from m3.quality import evaluate, read_labels


class QualityTests(unittest.TestCase):
    def test_metrics_distinguish_abstention_wrong_and_no_match(self):
        class FakeStore:
            def paper(self, kind, pid):
                if kind == 'ref':
                    return [{'ref_arxiv_id':'2606.00001'}]
                return [dict(citation_key='correct', url='2606.00001'),
                        dict(citation_key='missed'), dict(citation_key='wrong',url='2606.00001'),
                        dict(citation_key='negative')]
        human = {( '2606.01000', k): {'groundtruth_arxiv_id': v} for k,v in
                 [('correct','2606.00001'),('missed','2606.00001'),('wrong',''),('negative','')]}
        groups, _ = evaluate(FakeStore(), [dict(arxiv_id='2606.01000',owner='24127192',partition='test')], human)
        self.assertEqual(groups['all']['precision'], .5)
        self.assertEqual(groups['all']['recall'], .5)
        self.assertEqual(groups['all']['coverage'], .5)
        self.assertEqual(groups['all']['accuracy'], .5)
        self.assertEqual(groups['partition:test']['reviewed'], 4)

    def test_full_auto_validation_merge_and_rejection(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            tmp = Path(directory)
            selection, bib, refs, human = [], [], [], []
            for i in range(505):
                pid = f'2606.{i+1000:05}'
                manual = i < 5
                selection.append(dict(arxiv_id=pid, owner='24127192', kind='manual' if manual else 'auto',partition='test' if i == 0 else 'train'))
                for j in range(20 if manual else 1):
                    target = f'2501.{j:05}'
                    bib.append(dict(arxiv_id=pid,citation_key=f'k{j}',url='https://arxiv.org/abs/'+target+'v2'))
                    refs.append(dict(arxiv_id=pid,ref_arxiv_id=target))
                    if manual:
                        human.append(dict(arxiv_id=pid,citation_key=f'k{j}',groundtruth_arxiv_id=target,label_source='manual'))
            write_csv(tmp/'selection.csv',selection,list(selection[0]))
            write_csv(tmp/'bib.csv',bib,list(bib[0]))
            write_csv(tmp/'refs.csv',refs,list(refs[0]))
            write_csv(tmp/'manual.csv',human)
            shared = ['--selection',str(tmp/'selection.csv'),'--bibtex',str(tmp/'bib.csv'),'--references',str(tmp/'refs.csv'),'--cache',str(tmp/'index.sqlite')]
            def run(script, extra):
                return subprocess.run([sys.executable,str(ROOT/'src'/script), *shared,*extra],cwd=ROOT,capture_output=True,text=True)
            result = run('label_auto.py',['--output',str(tmp/'auto.csv')])
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(len(read_labels(tmp/'auto.csv')),500)
            extra = ['--manual',str(tmp/'manual.csv'),'--auto',str(tmp/'auto.csv'),'--output',str(tmp/'labels.csv')]
            result = run('check_labels.py', extra)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(len(read_labels(tmp/'labels.csv')),600)
            report = json.loads((tmp/'labels.csv.report.json').read_text())
            self.assertTrue(report['valid'])
            self.assertEqual(report['metrics']['all']['precision'],1)
            human[0]['groundtruth_arxiv_id'] = '2401.99999'
            write_csv(tmp/'manual.csv',human)
            result = run('check_labels.py',extra[:-1]+[str(tmp/'invalid.csv')])
            self.assertEqual(result.returncode,1,result.stderr)
            self.assertFalse((tmp/'invalid.csv').exists())
            report = json.loads((tmp/'invalid.csv.report.json').read_text())
            self.assertFalse(report['valid'])
            self.assertIsNone(report['metrics'])

    def test_duplicate_rows_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            p=Path(directory)/'labels.csv'
            row=dict(zip(FIELDS,['2606.01000','k','2501.00001','manual']))
            write_csv(p,[row,row])
            with self.assertRaisesRegex(ValueError,'duplicate'):
                read_labels(p)


if __name__ == '__main__':
    unittest.main()
