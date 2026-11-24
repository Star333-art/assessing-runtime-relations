import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

SCRIPT=Path(__file__).with_name('run_heterogeneous_graph_component_ablation.py')


def frozen_case(tmp_path):
    folder=tmp_path/'input/baro_only'
    folder.mkdir(parents=True)
    path=folder/'case.json'
    path.write_text(json.dumps(dict(dataset_id='case',fault_type='cpu',ground_truth='service',
        metrics=dict(rank=3,top1=0,top3=1,top5=1,avg5=1/3))),encoding='utf-8')
    return folder.parent,path


def test_replay_uses_separate_output_and_keeps_frozen_bytes(tmp_path):
    source,path=frozen_case(tmp_path)
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    output=tmp_path/'output'
    subprocess.run([sys.executable,str(SCRIPT),'--input',str(source),'--output',str(output),
        '--components','baro_only','--replay-existing'],check=True,capture_output=True)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    assert not (source/'component_ablation_cases.csv').exists()
    row=pd.read_csv(output/'component_ablation_cases.csv').iloc[0]
    assert row['rank']==3 and row['avg5']==pytest.approx(1/3)


@pytest.mark.parametrize('nested',[False,True])
def test_force_cannot_write_into_frozen_input(tmp_path,nested):
    source,path=frozen_case(tmp_path)
    output=source/'nested' if nested else source
    result=subprocess.run([sys.executable,str(SCRIPT),'--input',str(source),'--output',str(output),
        '--components','baro_only','--replay-existing','--force'],capture_output=True,text=True)
    assert result.returncode!=0 and 'separate from frozen inputs' in result.stderr
    assert path.exists() and not (output/'run_config.json').exists()
