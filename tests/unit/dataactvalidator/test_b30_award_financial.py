from tests.unit.dataactcore.factories.domain import TASFactory
from tests.unit.dataactcore.factories.job import SubmissionFactory
from tests.unit.dataactcore.factories.staging import AwardFinancialFactory, ObjectClassProgramActivityFactory
from tests.unit.dataactvalidator.utils import number_of_errors, query_columns


_FILE = "b30_award_financial"


def test_column_headers(database):
    expected_subset = {
        "source_row_number",
        "source_value_tas",
        "source_value_object_class",
        "source_value_program_activity_code",
        "source_value_program_activity_name",
        "source_value_transaction_obligated_amount",
        "difference",
        "uniqueid_TAS",
        "uniqueid_ObjectClass",
        "uniqueid_ProgramActivityCode",
        "uniqueid_ProgramActivityName",
    }
    actual = set(query_columns(_FILE, database))
    assert actual == expected_subset


def test_success(database):
    """Obligations reported in File C are backed by a File B row with the same TAS, object class, and program
    activity code+name.
    """
    tas = TASFactory()
    tas2 = TASFactory()
    database.session.add_all([tas, tas2])
    database.session.flush()

    submission = SubmissionFactory(submission_id=1, reporting_fiscal_year=2025)

    op = ObjectClassProgramActivityFactory(
        account_num=tas.account_num, program_activity_code="1", program_activity_name="PA1", object_class="1"
    )
    op2 = ObjectClassProgramActivityFactory(
        account_num=tas2.account_num, program_activity_code="2", program_activity_name="PA2", object_class="0"
    )

    # Exact match
    af = AwardFinancialFactory(
        account_num=tas.account_num,
        program_activity_code="1",
        program_activity_name="pa1",
        object_class="1",
        transaction_obligated_amou=-1000,
    )
    # Object classes of all zeros are treated as equivalent, program activity name matching is case insensitive
    af2 = AwardFinancialFactory(
        account_num=tas2.account_num,
        program_activity_code="2",
        program_activity_name="pA2",
        object_class="0000",
        transaction_obligated_amou=-1000,
    )
    # Program activity code 0000 is not compared, only the name has to match
    af3 = AwardFinancialFactory(
        account_num=tas.account_num,
        program_activity_code="0000",
        program_activity_name="PA1",
        object_class="1",
        transaction_obligated_amou=-1000,
    )

    assert number_of_errors(_FILE, database, submission=submission, models=[op, op2, af, af2, af3]) == 0


def test_success_no_obligation(database):
    """Rows with no obligation (null or zero TransactionObligatedAmount) are not checked, they hold balance data
    rather than award obligations.
    """
    tas = TASFactory()
    database.session.add(tas)
    database.session.flush()

    submission = SubmissionFactory(submission_id=1, reporting_fiscal_year=2025)

    op = ObjectClassProgramActivityFactory(
        account_num=tas.account_num, program_activity_code="1", program_activity_name="PA1", object_class="1"
    )

    # Null obligation with no matching File B row
    af = AwardFinancialFactory(
        account_num=tas.account_num,
        program_activity_code="2",
        program_activity_name="PA2",
        object_class="1",
        transaction_obligated_amou=None,
    )
    # Zero obligation with no matching File B row
    af2 = AwardFinancialFactory(
        account_num=tas.account_num,
        program_activity_code="2",
        program_activity_name="PA2",
        object_class="1",
        transaction_obligated_amou=0,
    )

    assert number_of_errors(_FILE, database, submission=submission, models=[op, af, af2]) == 0


def test_success_no_program_activity(database):
    """Rows without a program activity code or name cannot be matched to a program activity and are left to the
    rules that require program activity to be reported.
    """
    tas = TASFactory()
    database.session.add(tas)
    database.session.flush()

    submission = SubmissionFactory(submission_id=1, reporting_fiscal_year=2025)

    op = ObjectClassProgramActivityFactory(
        account_num=tas.account_num, program_activity_code="1", program_activity_name="PA1", object_class="1"
    )
    af = AwardFinancialFactory(
        account_num=tas.account_num,
        program_activity_code=None,
        program_activity_name="",
        object_class="2",
        transaction_obligated_amou=-1000,
    )

    assert number_of_errors(_FILE, database, submission=submission, models=[op, af]) == 0


def test_success_optional_program_activity_pre_fy21(database):
    """Program activity was optional prior to FY21, those placeholder rows are not flagged."""
    tas = TASFactory()
    database.session.add(tas)
    database.session.flush()

    submission = SubmissionFactory(submission_id=1, reporting_fiscal_year=2020)

    op = ObjectClassProgramActivityFactory(
        account_num=tas.account_num, program_activity_code="1", program_activity_name="PA1", object_class="1"
    )
    af = AwardFinancialFactory(
        account_num=tas.account_num,
        program_activity_code="OPTN",
        program_activity_name="Field is optional prior to FY21",
        object_class="1",
        transaction_obligated_amou=-1000,
    )

    assert number_of_errors(_FILE, database, submission=submission, models=[op, af]) == 0


def test_failure(database):
    """Obligations reported in File C with no File B row for the same TAS, object class, and program activity."""
    tas = TASFactory()
    tas2 = TASFactory()
    database.session.add_all([tas, tas2])
    database.session.flush()

    submission = SubmissionFactory(submission_id=1, reporting_fiscal_year=2025)

    op = ObjectClassProgramActivityFactory(
        account_num=tas.account_num, program_activity_code="1", program_activity_name="PA1", object_class="1"
    )

    # Program activity not in File B
    af = AwardFinancialFactory(
        account_num=tas.account_num,
        program_activity_code="2",
        program_activity_name="PA2",
        object_class="1",
        transaction_obligated_amou=-1000,
    )
    # Object class not in File B for that program activity
    af2 = AwardFinancialFactory(
        account_num=tas.account_num,
        program_activity_code="1",
        program_activity_name="PA1",
        object_class="2",
        transaction_obligated_amou=-1000,
    )
    # TAS not in File B
    af3 = AwardFinancialFactory(
        account_num=tas2.account_num,
        program_activity_code="1",
        program_activity_name="PA1",
        object_class="1",
        transaction_obligated_amou=-1000,
    )
    # Positive obligations are checked the same way as negative ones
    af4 = AwardFinancialFactory(
        account_num=tas.account_num,
        program_activity_code="3",
        program_activity_name="PA3",
        object_class="1",
        transaction_obligated_amou=1000,
    )
    # Program activity was only optional prior to FY21
    af5 = AwardFinancialFactory(
        account_num=tas.account_num,
        program_activity_code="OPTN",
        program_activity_name="Field is optional prior to FY21",
        object_class="1",
        transaction_obligated_amou=-1000,
    )

    assert number_of_errors(_FILE, database, submission=submission, models=[op, af, af2, af3, af4, af5]) == 5
