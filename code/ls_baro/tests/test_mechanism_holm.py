"""Protect global correction, cohort pairing, and the frozen 50-test replay."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "code/ls_baro/tools/evaluate_mechanism_holm.py"
spec = importlib.util.spec_from_file_location("mechanism_holm", SCRIPT)
holm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(holm)


@pytest.fixture(scope="module")
def cases():
    evidence = ROOT / "results/core_evidence"
    return holm.load_cases(evidence / "relation_mechanism_cases.csv",
        evidence / "microcercl_relation_cases.csv", evidence / "microcercl_shared_source_case_results.csv")


def test_holm_restores_original_order_and_handles_ties():
    np.testing.assert_allclose(holm.holm_adjust([0.04, 0.01, 0.01, 1.0]), [0.08, 0.04, 0.04, 1.0])


def test_frozen_replay_global_counts_and_ownership_support(cases):
    contrasts, tasks = holm.evaluate(cases)
    assert len(contrasts) == 50
    assert int(contrasts.raw_below_005.sum()) == 29
    assert int(contrasts.holm_below_005.sum()) == 16
    counts = tasks.set_index(holm.TASK_COLUMNS).holm_below_005
    assert counts.loc[("lsdb", "ownership", "pod")] == 0
    assert counts.loc[("lsdb", "ownership", "service")] == 5
    assert counts.loc[("microcercl", "ownership", "pod")] == 5
    zero = contrasts[contrasts.nonzero_differences.eq(0)]
    assert len(zero) == 4
    assert zero.p_raw.eq(1).all() and zero.p_holm.eq(1).all()


def test_duplicate_case_condition_rows_are_rejected(cases):
    duplicated = holm.pd.concat([cases, cases.iloc[:1]], ignore_index=True)
    with pytest.raises(ValueError, match="Duplicate case-condition"):
        holm.evaluate(duplicated)


def test_unmatched_control_is_rejected(cases):
    with pytest.raises(ValueError, match="Unmatched controls"):
        holm.evaluate(cases.iloc[1:])


def test_incorrect_cohort_size_is_rejected(cases):
    first = cases.iloc[0]
    remove = (cases.dataset.eq(first.dataset) & cases.relation.eq(first.relation)
              & cases.root_layer.eq(first.root_layer) & cases.case_id.eq(first.case_id))
    with pytest.raises(ValueError, match="Unexpected cohort size"):
        holm.evaluate(cases[~remove])


def test_nonfinite_score_is_rejected(cases):
    invalid = cases.copy()
    invalid.loc[invalid.index[0], "tie_avg5"] = np.nan
    with pytest.raises(ValueError, match="finite"):
        holm.evaluate(invalid)
