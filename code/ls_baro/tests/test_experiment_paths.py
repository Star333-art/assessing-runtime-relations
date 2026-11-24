from ls_baro.experiment_paths import dataset_path, output_path


def test_explicit_locations_support_spaces_and_do_not_depend_on_cwd(tmp_path,monkeypatch):
    work=tmp_path/'run outputs'
    data=tmp_path/'raw data'
    monkeypatch.setenv('LS_BARO_OUTPUT_ROOT',str(work))
    monkeypatch.setenv('LS_BARO_LSDB_ROOT',str(data))
    monkeypatch.chdir(tmp_path)
    assert output_path('stage/cases.csv')==work/'stage/cases.csv'
    assert dataset_path('lsdb')==data


def test_source_keys_do_not_capture_machine_paths(tmp_path,monkeypatch):
    from ls_baro.experiment_paths import source_key, PACKAGE_ROOT
    monkeypatch.setenv('LS_BARO_OUTPUT_ROOT',str(tmp_path/'outputs'))
    assert source_key(tmp_path/'outputs/stage/cases.csv')=='reproduced/raw/stage/cases.csv'
    assert source_key(PACKAGE_ROOT/'tools/audit_final_requirements.py')=='code/ls_baro/tools/audit_final_requirements.py'
