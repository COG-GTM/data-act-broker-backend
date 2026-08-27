import csv
import os

from decimal import Decimal

from dataactcore.models.lookups import PUBLISH_STATUS_DICT, RULE_SEVERITY_DICT
from dataactcore.models.validationModels import RuleSql
from dataactcore.utils.analyst_reports import (
    AHEAD_OF_PLAN,
    BEHIND_PLAN,
    BURN_RATE_HEADERS,
    NO_PLAN,
    ON_PLAN,
    UNMATCHED_OBLIGATION_HEADERS,
    UNMATCHED_OBLIGATION_RULE_LABEL,
    burn_rate_rows,
    fiscal_year_submissions,
    unmatched_obligations,
    write_burn_rate_report,
    write_unmatched_obligation_report,
)
from dataactvalidator.filestreaming.sqlLoader import SQLLoader

from tests.unit.dataactcore.factories.domain import CGACFactory, TASFactory
from tests.unit.dataactcore.factories.job import SubmissionFactory
from tests.unit.dataactcore.factories.staging import (
    AppropriationFactory,
    AwardFinancialFactory,
    ObjectClassProgramActivityFactory,
    PublishedAppropriationFactory,
    PublishedObjectClassProgramActivityFactory,
)
from tests.unit.dataactvalidator.utils import populate_publish_status


def load_b30_rule(database):
    """Load the rule the exception report is built from, the way the validator does"""
    rule = RuleSql(
        rule_sql=SQLLoader.read_sql_str("b30_award_financial"),
        rule_label=UNMATCHED_OBLIGATION_RULE_LABEL,
        rule_error_message="Unmatched obligation",
        rule_cross_file_flag=True,
        rule_severity_id=RULE_SEVERITY_DICT["warning"],
    )
    database.session.add(rule)
    database.session.commit()


def read_report(report_path, file_name):
    with open(os.path.join(report_path, file_name), "r") as report_file:
        return list(csv.reader(report_file))


def test_unmatched_obligations_aggregates_rows(database, validation_constants):
    """Rows flagged by the rule are rolled up to one line per TAS/program activity/object class"""
    load_b30_rule(database)

    tas = TASFactory()
    database.session.add(tas)
    database.session.flush()

    submission = SubmissionFactory(submission_id=1, cgac_code="097", frec_code=None, reporting_fiscal_year=2025)
    database.session.add(submission)

    # two File C rows for the same program activity with no File B backing, plus one that is backed
    unbacked_1 = AwardFinancialFactory(
        submission_id=1,
        account_num=tas.account_num,
        display_tas="097-1234",
        program_activity_code="0001",
        program_activity_name="PA1",
        object_class="101",
        transaction_obligated_amou=-100,
    )
    unbacked_2 = AwardFinancialFactory(
        submission_id=1,
        account_num=tas.account_num,
        display_tas="097-1234",
        program_activity_code="0001",
        program_activity_name="PA1",
        object_class="101",
        transaction_obligated_amou=-50,
    )
    backed = AwardFinancialFactory(
        submission_id=1,
        account_num=tas.account_num,
        display_tas="097-1234",
        program_activity_code="0002",
        program_activity_name="PA2",
        object_class="202",
        transaction_obligated_amou=-25,
    )
    op = ObjectClassProgramActivityFactory(
        submission_id=1,
        account_num=tas.account_num,
        program_activity_code="0002",
        program_activity_name="PA2",
        object_class="202",
    )
    database.session.add_all([unbacked_1, unbacked_2, backed, op])
    database.session.commit()

    exceptions = unmatched_obligations(1)
    assert len(exceptions) == 1
    assert exceptions[0]["tas"] == "097-1234"
    assert exceptions[0]["program_activity_code"] == "0001"
    assert exceptions[0]["obligated_amount"] == Decimal(-150)
    assert exceptions[0]["row_count"] == 2


def test_write_unmatched_obligation_report(database, validation_constants, tmpdir):
    """The exception report has one row per exception with the agency, period, amount, and a plain-English reason"""
    load_b30_rule(database)

    cgac = CGACFactory(cgac_code="097", agency_name="Department of Defense")
    tas = TASFactory()
    database.session.add_all([cgac, tas])
    database.session.flush()

    submission = SubmissionFactory(
        submission_id=1, cgac_code="097", frec_code=None, reporting_fiscal_year=2025, reporting_fiscal_period=7
    )
    award_financial = AwardFinancialFactory(
        submission_id=1,
        account_num=tas.account_num,
        display_tas="097-1234",
        program_activity_code="0001",
        program_activity_name="PA1",
        object_class="101",
        transaction_obligated_amou=-150,
    )
    database.session.add_all([submission, award_financial])
    database.session.commit()

    report_path = str(tmpdir) + os.path.sep
    file_name = write_unmatched_obligation_report(submission, report_path)
    rows = read_report(report_path, file_name)

    assert file_name == "SubID-1_File-C-to-B-unmatched-obligations-report_FY25P07.csv"
    assert rows[0] == UNMATCHED_OBLIGATION_HEADERS
    assert len(rows) == 2
    assert rows[1][:8] == [
        "Department of Defense",
        "097-1234",
        "2025",
        "7",
        "0001",
        "PA1",
        "101",
        "-150",
    ]
    assert "($150.00) of award obligations were reported in File C" in rows[1][9]
    assert "File B reports no obligations" in rows[1][9]


def test_unmatched_obligation_report_no_exceptions(database, validation_constants, tmpdir):
    """A submission with no unmatched obligations gets a headers-only report"""
    load_b30_rule(database)

    submission = SubmissionFactory(
        submission_id=1, cgac_code="097", frec_code=None, reporting_fiscal_year=2025, reporting_fiscal_period=7
    )
    database.session.add(submission)
    database.session.commit()

    report_path = str(tmpdir) + os.path.sep
    rows = read_report(report_path, write_unmatched_obligation_report(submission, report_path))
    assert rows == [UNMATCHED_OBLIGATION_HEADERS]


def test_burn_rate_rows_flags_deviations(database):
    """Program activities materially off a straight-line plan are called out, the ones on plan are not"""
    populate_publish_status(database)

    submission = SubmissionFactory(
        submission_id=1, cgac_code="097", frec_code=None, reporting_fiscal_year=2025, reporting_fiscal_period=6
    )
    # half the fiscal year is done, so the straight-line plan for the period is half the amount available
    appropriation = AppropriationFactory(submission_id=1, display_tas="097-1234", total_budgetary_resources_cpe=1000)
    on_plan = ObjectClassProgramActivityFactory(
        submission_id=1,
        display_tas="097-1234",
        program_activity_code="0001",
        program_activity_name="PA1",
        obligations_incurred_by_pr_cpe=-250,
    )
    ahead = ObjectClassProgramActivityFactory(
        submission_id=1,
        display_tas="097-1234",
        program_activity_code="0002",
        program_activity_name="PA2",
        obligations_incurred_by_pr_cpe=-250,
    )
    database.session.add_all([submission, appropriation, on_plan, ahead])
    database.session.commit()

    rows = {row["program_activity_code"]: row for row in burn_rate_rows(submission)}
    assert len(rows) == 2

    # each program activity is planned its share of the TAS's amount available, so both are planned 500 for the year
    assert rows["0001"]["cumulative_obligations"] == Decimal(250)
    assert rows["0001"]["straight_line_plan"] == Decimal(250)
    assert rows["0001"]["status"] == ON_PLAN
    assert rows["0002"]["status"] == ON_PLAN

    # the account has now obligated 650 of the 1000 available halfway through the year, past the threshold
    ahead.obligations_incurred_by_pr_cpe = -400
    database.session.commit()
    rows = {row["program_activity_code"]: row for row in burn_rate_rows(submission)}
    assert rows["0001"]["status"] == AHEAD_OF_PLAN
    assert rows["0002"]["status"] == AHEAD_OF_PLAN

    # only 350 of the 1000 available obligated halfway through the year is materially behind
    ahead.obligations_incurred_by_pr_cpe = -100
    database.session.commit()
    rows = {row["program_activity_code"]: row for row in burn_rate_rows(submission)}
    assert rows["0001"]["status"] == BEHIND_PLAN
    assert rows["0002"]["status"] == BEHIND_PLAN


def test_burn_rate_rows_no_amount_available(database):
    """Program activities whose TAS reports nothing in File A are called out rather than compared to a zero plan"""
    submission = SubmissionFactory(
        submission_id=1, cgac_code="097", frec_code=None, reporting_fiscal_year=2025, reporting_fiscal_period=6
    )
    ocpa = ObjectClassProgramActivityFactory(
        submission_id=1,
        display_tas="097-1234",
        program_activity_code="0001",
        program_activity_name="PA1",
        obligations_incurred_by_pr_cpe=-250,
    )
    database.session.add_all([submission, ocpa])
    database.session.commit()

    rows = burn_rate_rows(submission)
    assert len(rows) == 1
    assert rows[0]["straight_line_plan"] == Decimal(0)
    assert rows[0]["variance_pct"] is None
    assert rows[0]["status"] == NO_PLAN


def test_write_burn_rate_report_includes_published_periods(database, tmpdir):
    """The burn rate report covers the fiscal year to date, using published data for the earlier periods"""
    populate_publish_status(database)

    cgac = CGACFactory(cgac_code="097", agency_name="Department of Defense")
    database.session.add(cgac)

    published_sub = SubmissionFactory(
        submission_id=1,
        cgac_code="097",
        frec_code=None,
        reporting_fiscal_year=2025,
        reporting_fiscal_period=3,
        is_fabs=False,
        publish_status_id=PUBLISH_STATUS_DICT["published"],
    )
    current_sub = SubmissionFactory(
        submission_id=2,
        cgac_code="097",
        frec_code=None,
        reporting_fiscal_year=2025,
        reporting_fiscal_period=6,
        is_fabs=False,
        publish_status_id=PUBLISH_STATUS_DICT["unpublished"],
    )
    database.session.add_all(
        [
            published_sub,
            current_sub,
            PublishedAppropriationFactory(submission_id=1, display_tas="097-1234", total_budgetary_resources_cpe=1000),
            PublishedObjectClassProgramActivityFactory(
                submission_id=1,
                display_tas="097-1234",
                program_activity_code="0001",
                program_activity_name="PA1",
                obligations_incurred_by_pr_cpe=-250,
            ),
            AppropriationFactory(submission_id=2, display_tas="097-1234", total_budgetary_resources_cpe=1000),
            ObjectClassProgramActivityFactory(
                submission_id=2,
                display_tas="097-1234",
                program_activity_code="0001",
                program_activity_name="PA1",
                obligations_incurred_by_pr_cpe=-800,
            ),
        ]
    )
    database.session.commit()

    report_path = str(tmpdir) + os.path.sep
    file_name = write_burn_rate_report(current_sub, report_path)
    rows = read_report(report_path, file_name)

    assert file_name == "SubID-2_File-B-burn-rate-report_FY25P06.csv"
    assert rows[0] == BURN_RATE_HEADERS
    assert len(rows) == 3

    # period 3 is a quarter of the way through the year, 250 of 1000 is on plan
    assert rows[1][3] == "3"
    assert rows[1][11] == ON_PLAN

    # period 6 is halfway through the year, 800 of 1000 is well ahead of it
    assert rows[2][3] == "6"
    assert rows[2][11] == AHEAD_OF_PLAN
    assert "ahead of plan" in rows[2][12]


def test_fiscal_year_submissions_one_submission_per_period(database):
    """A period reported by more than one submission is only counted once, using the most recent submission"""
    populate_publish_status(database)

    submissions = [
        SubmissionFactory(
            submission_id=submission_id,
            cgac_code="097",
            frec_code=None,
            reporting_fiscal_year=2025,
            reporting_fiscal_period=period,
            is_fabs=False,
            publish_status_id=PUBLISH_STATUS_DICT[publish_status],
        )
        for submission_id, period, publish_status in (
            (1, 3, "published"),
            (2, 3, "updated"),
            (3, 6, "published"),
            (4, 6, "unpublished"),
        )
    ]
    database.session.add_all(submissions)
    database.session.commit()

    fy_submissions = fiscal_year_submissions(submissions[3])

    assert [(sub.submission_id, is_current) for sub, is_current in fy_submissions] == [(2, False), (4, True)]
