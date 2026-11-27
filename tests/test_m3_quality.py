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
from m3.quality import evaluate, read_labels, validate


class QualityTests(unittest.TestCase):
    def test_metrics_only_positive_pairs(self):
        class FakeStore:
            def paper(self, kind, pid):
                if kind == 'ref':
                    return [{'ref_arxiv_id':'2606.00001'}, {'ref_arxiv_id':'2606.00002'}]
                return [dict(citation_key='correct', url='2606.00001'),
                        dict(citation_key='missed'), dict(citation_key='wrong',url='2606.00002'),
                        dict(citation_key='unknown',url='2606.00001')]
        human = {('2606.01000', k): {'groundtruth_arxiv_id': '2606.00001'}
                 for k in ('correct','missed','wrong')}
        chosen = [dict(arxiv_id='2606.01000',owner='24127192',partition='test')]
        groups, details = evaluate(FakeStore(), chosen, human)
        self.assertEqual(groups['all']['precision'], .5)
        self.assertAlmostEqual(groups['all']['recall'], 1/3)
        self.assertAlmostEqual(groups['all']['coverage'], 2/3)
        self.assertAlmostEqual(groups['all']['accuracy'], 1/3)
        self.assertEqual(groups['partition:test']['reviewed'], 3)
        self.assertEqual(len(details),3)
        human['2606.01000','missed']['groundtruth_arxiv_id'] = ''
        with self.assertRaisesRegex(ValueError,'positive'):
            evaluate(FakeStore(), chosen, human)

    def test_sparse_manual_and_zero_match_auto(self):
        class FakeStore:
            def paper(self, kind, pid):
                return ([{'ref_arxiv_id':'2501.00001'}] if kind == 'ref' else
                        [{'citation_key':f'k{i}'} for i in range(21)])
        chosen = [dict(arxiv_id=f'2606.{i:05}',owner='24127192',partition='train') for i in range(5)]
        labels = {(p['arxiv_id'],f'k{i}'):dict(arxiv_id=p['arxiv_id'],citation_key=f'k{i}',groundtruth_arxiv_id='2501.00001',label_source='manual') for p in chosen for i in range(20)}
        self.assertTrue(validate(FakeStore(),chosen,labels,'manual')['valid'])
        labels.pop((chosen[0]['arxiv_id'],'k0'))
        self.assertFalse(validate(FakeStore(),chosen,labels,'manual')['valid'])
        auto = [dict(arxiv_id=f'2606.{i:05}',owner='24127192',partition='train') for i in range(500)]
        result = validate(FakeStore(),auto,{},'auto')
        self.assertFalse(result['valid'])
        self.assertEqual(result['owners']['24127192']['labelled_papers'],0)
        blank = dict(arxiv_id=chosen[0]['arxiv_id'],citation_key='k20',groundtruth_arxiv_id='',label_source='manual')
        labels[blank['arxiv_id'],'k20']=blank
        self.assertTrue(any('empty groundtruth' in e for e in validate(FakeStore(),chosen,labels,'manual')['errors']))

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
                # Extra entries remain unknown; no dummy negative labels.
                bib.append(dict(arxiv_id=pid,citation_key='unlabelled',url=''))
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
            with (tmp/'auto.csv.audit.csv').open() as f:
                audit = list(csv.DictReader(f))
            self.assertEqual(len(audit),1000)
            self.assertEqual(sum(not r['groundtruth_arxiv_id'] for r in audit),500)
            self.assertTrue(all(r['arxiv_id'] not in {p['arxiv_id'] for p in selection[:5]} for r in audit))
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
            for row in bib:
                row['url']=''
            write_csv(tmp/'bib.csv',bib,list(bib[0]))
            result = run('label_auto.py',['--output',str(tmp/'empty_auto.csv')])
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(read_labels(tmp/'empty_auto.csv'),{})
            with (tmp/'empty_auto.csv.papers.csv').open() as f:
                self.assertTrue(all(r['status']=='no_matches' for r in csv.DictReader(f)))

    def test_manual_interaction_saves_pairs_without_finalize(self):
        from unittest.mock import patch
        import label_tool
        class FakeStore:
            def paper(self, kind, pid):
                if kind == 'ref':
                    return [{'ref_arxiv_id':'2501.00001'}, {'ref_arxiv_id':'2501.00002'}]
                return [{'citation_key':'chosen'}, {'citation_key':'unknown'}]
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            output = Path(directory)/'manual.csv'
            argv=['label_tool.py','--selection','unused','--bibtex','unused','--references','unused','--cache','unused','--output',str(output)]
            selected=[dict(arxiv_id='2606.01000',owner='24127192',partition='test',n_any_hit='20')]
            # Assign, decline a conflicting reassignment, then skip the reference.
            with patch.object(sys,'argv',argv), patch.object(label_tool,'selection',return_value=selected), patch.object(label_tool,'open_store',return_value=FakeStore()), patch('builtins.input',side_effect=['key:chosen','key:chosen','NO','skip']), patch('builtins.print'):
                label_tool.main()
            labels=read_labels(output)
            self.assertEqual(len(labels),1)
            self.assertEqual(labels['2606.01000','chosen']['groundtruth_arxiv_id'],'2501.00001')
            # Resume and remove an incorrect label; do not create a blank row.
            with patch.object(sys,'argv',argv), patch.object(label_tool,'selection',return_value=selected), patch.object(label_tool,'open_store',return_value=FakeStore()), patch('builtins.input',side_effect=['clear:chosen','quit']), patch('builtins.print'):
                label_tool.main()
            self.assertEqual(read_labels(output),{})

    def test_duplicate_rows_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            p=Path(directory)/'labels.csv'
            row=dict(zip(FIELDS,['2606.01000','k','2501.00001','manual']))
            write_csv(p,[row,row])
            with self.assertRaisesRegex(ValueError,'duplicate'):
                read_labels(p)


if __name__ == '__main__':
    unittest.main()
