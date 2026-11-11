"""data verify submit | list through the service and the command line (synthetic records)."""

import json

import pytest

from tests.test_verifications import OTHER, RECORD
from wequant.cli import main
from wequant.service import (
    Context,
    Exit,
    ServiceError,
    VerifyListRequest,
    VerifyPage,
    VerifySubmitRequest,
    VerifySubmitResult,
    data_verify_list,
    data_verify_submit,
)

LATER = {
    **RECORD,
    "object": {**RECORD["object"], "date": "2017-03-01"},
    "verifier": {**RECORD["verifier"], "name": "u"},
}


def test_a_submission_with_a_bad_record_adds_nothing_and_locates_the_fault(tmp_path):
    bad = {**RECORD, "values": {"tencent": {"close": 1.0}}}
    with pytest.raises(ServiceError) as raised:
        data_verify_submit(VerifySubmitRequest(records=[RECORD, bad]), Context(tmp_path))
    assert raised.value.exit == Exit.USAGE
    assert [(d.code, d.loc) for d in raised.value.detail] == [("bad_ref", ["records", 1, "values", "tencent"])]
    assert data_verify_list(VerifyListRequest(), Context(tmp_path)).total == 0


def test_the_list_filters_by_cell_date_and_verifier_and_pages(tmp_path):
    context = Context(tmp_path)
    ids = data_verify_submit(VerifySubmitRequest(records=[RECORD, OTHER, LATER]), context).ids
    found = data_verify_list(VerifyListRequest(start="2017-01-01"), context)
    assert [r.id for r in found.rows] == [ids[2]]
    assert data_verify_list(VerifyListRequest(verifier="checker"), context).total == 2
    page = data_verify_list(VerifyListRequest(limit=1, offset=1), context)
    assert (page.total, [r.id for r in page.rows]) == (3, [ids[1]])


def test_the_command_takes_json_lines_and_prints_the_models(tmp_path, capsys):
    path = tmp_path / "records.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in (RECORD, OTHER)) + "\n")
    with pytest.raises(SystemExit) as exited:
        main(["data", "verify", "submit", str(path), "--json", "--home", str(tmp_path)])
    submitted = VerifySubmitResult.model_validate_json(capsys.readouterr().out)
    assert exited.value.code == 0 and (submitted.added, submitted.existing) == (2, 0)
    with pytest.raises(SystemExit):
        main(["data", "verify", "list", "--code", "600822.SH", "--json", "--home", str(tmp_path)])
    assert [r.id for r in VerifyPage.model_validate_json(capsys.readouterr().out).rows] == submitted.ids
