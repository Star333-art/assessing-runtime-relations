from types import SimpleNamespace
import pytest
from ls_baro.mechanism_labels import mechanism_label


def contrast(point=2, low=1, high=3):
    return SimpleNamespace(difference_pp=point, ci95_low_pp=low, ci95_high_pp=high)


@pytest.mark.parametrize("index", range(4))
def test_each_of_four_controls_must_exclude_zero(index):
    controls = [contrast() for _ in range(4)]
    controls[index] = contrast(low=0)
    assert mechanism_label(contrast(), controls) == "Recovery not separable from controls"


@pytest.mark.parametrize("total,label", [
    (contrast(), "Alignment-dependent recovery"),
    (contrast(low=-1), "Uncertain recovery"),
    (contrast(point=0,low=0,high=0), "No recovery"),
    (contrast(point=-1,low=-2,high=1), "No recovery"),
    (contrast(point=-2,low=-3,high=-1), "Harmful"),
])
def test_mapping_interval_and_estimate_define_recovery(total,label):
    assert mechanism_label(total,[contrast() for _ in range(4)]) == label


def test_missing_controls_and_nonfinite_intervals_are_rejected():
    with pytest.raises(ValueError,match="four"):
        mechanism_label(contrast(),[contrast()]*3)
    with pytest.raises(ValueError,match="finite"):
        mechanism_label(contrast(low=float('nan')),[contrast()]*4)
