from pathlib import Path
import sys
import pandas as pd
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from audit_final_requirements import validate_integrity, summarize_integrity


@pytest.fixture
def audit():
    eligible=pd.DataFrame([dict(dataset='lsdb',relation='deployment',root_layer='host',case_id='1')])
    rows=pd.DataFrame([dict(**eligible.iloc[0].to_dict(),replicate=i,edges=2,accepted_swaps=0,
        fraction_edges_changed=0,exact_source_degree=True,exact_target_degree=True,duplicate_free=True) for i in range(20)])
    return eligible,rows


def test_genuine_zero_swaps_are_retained_and_no_edge_placeholders_excluded(audit):
    eligible,rows=audit
    placeholder=rows.iloc[[0]].assign(case_id='excluded',replicate=-1,edges=0)
    actual=validate_integrity(pd.concat([rows,placeholder],ignore_index=True),eligible)
    summary=summarize_integrity(actual).iloc[0]
    assert summary.replicates==20 and summary.zero_swap_replicates==20
    assert summary.no_nontrivial_cases==1 and summary.mean_retained==1


def test_missing_replicates_or_cases_are_rejected(audit):
    eligible,rows=audit
    with pytest.raises(ValueError,match='exactly'):
        validate_integrity(rows.iloc[1:],eligible)
    with pytest.raises(ValueError,match='Missing'):
        validate_integrity(rows.iloc[:0],eligible)


def test_duplicates_and_integrity_failures_are_rejected(audit):
    eligible,rows=audit
    with pytest.raises(ValueError,match='Duplicate'):
        validate_integrity(pd.concat([rows,rows.iloc[:1]]),eligible)
    rows.loc[0,'exact_target_degree']=False
    with pytest.raises(ValueError,match='degree'):
        validate_integrity(rows,eligible)
