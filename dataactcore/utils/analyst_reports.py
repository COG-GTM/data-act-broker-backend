"""Analyst-facing reports derived from the File C to File B unmatched obligation rule (B30).

These are summary reports meant to be read by a financial analyst rather than row-level error dumps. They are written
in the same csv format, to the same location, and with the same naming conventions as the other downloadable
submission reports.
"""

import csv
import logging

from decimal import Decimal

from sqlalchemy import func

from dataactcore.interfaces.db import GlobalDB
from dataactcore.models.domainModels import CGAC, FREC
from dataactcore.models.jobModels import Submission
from dataactcore.models.lookups import PUBLISH_STATUS_DICT
from dataactcore.models.stagingModels import (
    Appropriation,
    ObjectClassProgramActivity,
    PublishedAppropriation,
    PublishedObjectClassProgramActivity,
)
from dataactcore.models.validationModels import RuleSql
from dataactcore.utils.report import analyst_report_file_name

logger = logging.getLogger(__name__)

# Rule providing the rows the exception report is built from
UNMATCHED_OBLIGATION_RULE_LABEL = "B30"

# Fiscal periods 2 through 12 make up a full fiscal year (periods 1 and 2 are reported together), so a full year of
# execution is complete at period 12
LAST_FISCAL_PERIOD = 12

# Share of the straight-line plan an agency has to be off by for the period to be called out. Execution is never
# perfectly linear, so small deviations are expected and are not worth an analyst's attention.
BURN_RATE_MATERIALITY_THRESHOLD = Decimal("0.10")

UNMATCHED_OBLIGATION_HEADERS = [
    "Agency",
    "TAS",
    "Fiscal Year",
    "Period",
    "Program Activity Code",
    "Program Activity Name",
    "Object Class",
    "Unmatched Obligated Amount",
    "File C Rows",
    "Reason",
]

BURN_RATE_HEADERS = [
    "Agency",
    "TAS",
    "Fiscal Year",
    "Period",
    "Program Activity Code",
    "Program Activity Name",
    "Cumulative Obligations",
    "Amount Available (File A, program activity share)",
    "Straight-Line Plan to Date",
    "Variance",
    "Variance Percent",
    "Execution Status",
    "Note",
]

ON_PLAN = "On plan"
AHEAD_OF_PLAN = "Ahead of plan"
BEHIND_PLAN = "Behind plan"
NO_PLAN = "No amount available in File A"


def agency_name(submission):
    """Get the name of the agency responsible for the submission.

    Args:
        submission: the submission to get the agency name for

    Returns:
        The agency name as loaded from the agency codes, an empty string if the agency isn't found
    """
    sess = GlobalDB.db().session

    cgac = sess.query(CGAC).filter_by(cgac_code=submission.cgac_code).one_or_none()
    if cgac:
        return cgac.agency_name

    frec = sess.query(FREC).filter_by(frec_code=submission.frec_code).one_or_none()
    if frec:
        return frec.agency_name

    return ""


def unmatched_obligations(submission_id):
    """Aggregate the rows flagged by the unmatched obligation rule to the level an analyst reviews.

    Args:
        submission_id: the submission to run the rule for

    Returns:
        A list of dicts, one per TAS/program activity/object class, ordered by the size of the unbacked obligation
    """
    sess = GlobalDB.db().session

    rule = sess.query(RuleSql).filter_by(rule_label=UNMATCHED_OBLIGATION_RULE_LABEL).one()
    failures = sess.execute(rule.rule_sql.format(submission_id)).fetchall()

    grouped = {}
    for failure in failures:
        key = (
            failure["source_value_tas"],
            failure["source_value_program_activity_code"],
            failure["source_value_program_activity_name"],
            failure["source_value_object_class"],
        )
        exception = grouped.setdefault(
            key,
            {
                "tas": failure["source_value_tas"],
                "program_activity_code": failure["source_value_program_activity_code"],
                "program_activity_name": failure["source_value_program_activity_name"],
                "object_class": failure["source_value_object_class"],
                "obligated_amount": Decimal(0),
                "row_count": 0,
            },
        )
        exception["obligated_amount"] += Decimal(failure["source_value_transaction_obligated_amount"])
        exception["row_count"] += 1

    return sorted(grouped.values(), key=lambda exception: abs(exception["obligated_amount"]), reverse=True)


def unmatched_obligation_reason(exception, submission):
    """Build the plain-English explanation of a single unmatched obligation.

    Args:
        exception: a single aggregated exception from unmatched_obligations
        submission: the submission the exception belongs to

    Returns:
        A sentence explaining what was found and what to do about it
    """
    program_activity = " / ".join(
        value for value in (exception["program_activity_code"], exception["program_activity_name"]) if value
    )
    return (
        "{} of award obligations were reported in File C for program activity {} (object class {}) under {} in "
        "FY{} period {}, but File B reports no obligations for that program activity and object class. Either the "
        "award activity is missing from File B or the program activity/object class on these File C rows is "
        "wrong.".format(
            format_currency(exception["obligated_amount"]),
            program_activity,
            exception["object_class"],
            exception["tas"],
            submission.reporting_fiscal_year,
            submission.reporting_fiscal_period,
        )
    )


def format_currency(amount):
    """Format an amount the way it is displayed to analysts.

    Args:
        amount: the amount to format

    Returns:
        The amount as a dollar string, negative amounts in parentheses as they are in financial statements
    """
    formatted = "${:,.2f}".format(abs(Decimal(amount)))
    return "({})".format(formatted) if amount < 0 else formatted


def write_unmatched_obligation_report(submission, report_path):
    """Write the exception report for obligations in File C that have no File B backing.

    Args:
        submission: the submission to report on
        report_path: the directory the report is written to

    Returns:
        The name of the report file that was written
    """
    file_name = analyst_report_file_name(submission, "unmatched_obligations")
    name_of_agency = agency_name(submission)

    with open("".join([report_path, file_name]), "w", newline="") as report_file:
        report_csv = csv.writer(report_file, delimiter=",", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        report_csv.writerow(UNMATCHED_OBLIGATION_HEADERS)

        for exception in unmatched_obligations(submission.submission_id):
            report_csv.writerow(
                [
                    name_of_agency,
                    exception["tas"],
                    submission.reporting_fiscal_year,
                    submission.reporting_fiscal_period,
                    exception["program_activity_code"],
                    exception["program_activity_name"],
                    exception["object_class"],
                    exception["obligated_amount"],
                    exception["row_count"],
                    unmatched_obligation_reason(exception, submission),
                ]
            )

    return file_name


def fiscal_year_submissions(submission):
    """Get the submissions making up the fiscal year to date for the submission's agency.

    Only published submissions are included alongside the submission being validated, since unpublished data for
    another period is still being worked on. A period reported more than once (a republished period, or a period
    also covered by the submission being validated) is represented by its most recent submission only, so the
    fiscal year to date is never double counted.

    Args:
        submission: the submission being validated

    Returns:
        A list of (submission, is_current) tuples, one per period, ordered by period
    """
    sess = GlobalDB.db().session

    published_subs = sess.query(Submission).filter(
        Submission.submission_id != submission.submission_id,
        Submission.is_fabs.is_(False),
        Submission.reporting_fiscal_year == submission.reporting_fiscal_year,
        Submission.reporting_fiscal_period <= submission.reporting_fiscal_period,
        Submission.publish_status_id.in_([PUBLISH_STATUS_DICT["published"], PUBLISH_STATUS_DICT["updated"]]),
    )
    if submission.cgac_code:
        published_subs = published_subs.filter(Submission.cgac_code == submission.cgac_code)
    else:
        published_subs = published_subs.filter(Submission.frec_code == submission.frec_code)

    by_period = {}
    for published_sub in published_subs.order_by(Submission.submission_id).all():
        by_period[published_sub.reporting_fiscal_period] = (published_sub, False)
    by_period[submission.reporting_fiscal_period] = (submission, True)

    return sorted(by_period.values(), key=lambda sub: sub[0].reporting_fiscal_period)


def program_activity_obligations(sub, is_current):
    """Get the cumulative obligations reported in File B by TAS and program activity.

    Args:
        sub: the submission to pull File B data for
        is_current: whether the submission is the one being validated (published data is used for the others)

    Returns:
        A list of (tas, program activity code, program activity name, obligations) tuples. Obligations are returned
        as positive amounts, File B reports them as negative
    """
    sess = GlobalDB.db().session
    model = ObjectClassProgramActivity if is_current else PublishedObjectClassProgramActivity

    results = (
        sess.query(
            model.display_tas,
            model.program_activity_code,
            model.program_activity_name,
            func.sum(model.obligations_incurred_by_pr_cpe),
        )
        .filter(model.submission_id == sub.submission_id)
        .group_by(model.display_tas, model.program_activity_code, model.program_activity_name)
        .all()
    )

    return [(tas, pac, pan, -Decimal(obligations or 0)) for tas, pac, pan, obligations in results]


def tas_amounts_available(sub, is_current):
    """Get the total budgetary resources reported in File A by TAS.

    Args:
        sub: the submission to pull File A data for
        is_current: whether the submission is the one being validated (published data is used for the others)

    Returns:
        A dict of TAS to the amount available for the period
    """
    sess = GlobalDB.db().session
    model = Appropriation if is_current else PublishedAppropriation

    results = (
        sess.query(model.display_tas, func.sum(model.total_budgetary_resources_cpe))
        .filter(model.submission_id == sub.submission_id)
        .group_by(model.display_tas)
        .all()
    )

    return {tas: Decimal(available or 0) for tas, available in results}


def baseline_shares(period_data):
    """Work out each program activity's share of its TAS's amount available.

    File A reports one amount available per TAS, so it has to be split across the TAS's program activities. The split
    used is the mix of obligations in the earliest period of the fiscal year, which is the agency's own plan for how
    the account is spent before execution has had a chance to drift.

    Args:
        period_data: the list of (submission, obligations, amounts available) tuples for the fiscal year to date

    Returns:
        A dict of (TAS, program activity code, program activity name) to that program activity's share of its TAS
    """
    shares = {}
    tas_baselined = set()

    for _, obligations, _ in period_data:
        obligations_by_tas = {}
        for tas, _, _, amount in obligations:
            obligations_by_tas[tas] = obligations_by_tas.get(tas, Decimal(0)) + amount

        for tas, pac, pan, amount in obligations:
            if tas in tas_baselined or not obligations_by_tas[tas]:
                continue
            shares[(tas, pac, pan)] = amount / obligations_by_tas[tas]

        tas_baselined.update(tas for tas in obligations_by_tas if obligations_by_tas[tas])

    return shares


def burn_rate_rows(submission, materiality=BURN_RATE_MATERIALITY_THRESHOLD):
    """Build the burn rate of each program activity against the amount available to its TAS.

    The plan is a straight line across the fiscal year, which is what an analyst eyeballs when checking whether
    execution is on track. Any period where obligations are off that line by more than the materiality threshold is
    called out.

    Args:
        submission: the submission being validated
        materiality: the share of the plan execution has to deviate by for the period to be called out

    Returns:
        A list of dicts, one per program activity per period, ordered by TAS, program activity, and period
    """
    period_data = [
        (fy_sub, program_activity_obligations(fy_sub, is_current), tas_amounts_available(fy_sub, is_current))
        for fy_sub, is_current in fiscal_year_submissions(submission)
    ]
    shares = baseline_shares(period_data)
    baseline_period = period_data[0][0].reporting_fiscal_period if period_data else None

    rows = []
    for fy_sub, obligations, available_by_tas in period_data:
        period = fy_sub.reporting_fiscal_period

        for tas, pac, pan, amount in obligations:
            planned = available_by_tas.get(tas, Decimal(0)) * shares.get((tas, pac, pan), Decimal(0))
            straight_line = planned * Decimal(period) / Decimal(LAST_FISCAL_PERIOD)

            variance = amount - straight_line
            variance_pct = variance / straight_line if straight_line else None

            if variance_pct is None:
                status = NO_PLAN
            elif abs(variance_pct) <= materiality:
                status = ON_PLAN
            elif variance_pct > 0:
                status = AHEAD_OF_PLAN
            else:
                status = BEHIND_PLAN

            rows.append(
                {
                    "tas": tas,
                    "program_activity_code": pac,
                    "program_activity_name": pan,
                    "fiscal_year": fy_sub.reporting_fiscal_year,
                    "period": period,
                    "cumulative_obligations": amount,
                    "amount_available": planned,
                    "straight_line_plan": straight_line,
                    "variance": variance,
                    "variance_pct": variance_pct,
                    "status": status,
                    "baseline_period": baseline_period,
                    "tas_amount_available": available_by_tas.get(tas, Decimal(0)),
                }
            )

    return sorted(rows, key=lambda row: (row["tas"], row["program_activity_code"] or "", row["period"]))


def burn_rate_note(row):
    """Build the plain-English explanation of a single period's burn rate.

    Args:
        row: a single row from burn_rate_rows

    Returns:
        A sentence explaining how execution compares to a straight-line plan
    """
    if row["status"] == NO_PLAN and not row["tas_amount_available"]:
        return (
            "No amount available was reported in File A for {}, so there is nothing to measure {} of obligations "
            "against.".format(row["tas"], format_currency(row["cumulative_obligations"]))
        )

    if row["status"] == NO_PLAN:
        return (
            "This program activity reported no obligations in period {}, the first period of the fiscal year "
            "reported for {}, so it has no share of the account's amount available to measure {} of obligations "
            "against.".format(row["baseline_period"], row["tas"], format_currency(row["cumulative_obligations"]))
        )

    comparison = "{} of the {} straight-line plan through period {}".format(
        format_currency(row["cumulative_obligations"]), format_currency(row["straight_line_plan"]), row["period"]
    )
    if row["status"] == ON_PLAN:
        return "Obligations are {}, within the expected range.".format(comparison)

    direction = "ahead of" if row["status"] == AHEAD_OF_PLAN else "behind"
    return "Obligations are {}, {} plan by {} ({:.1%}).".format(
        comparison, direction, format_currency(row["variance"]), row["variance_pct"]
    )


def write_burn_rate_report(submission, report_path):
    """Write the burn rate report for the submission's fiscal year to date.

    Args:
        submission: the submission to report on
        report_path: the directory the report is written to

    Returns:
        The name of the report file that was written
    """
    file_name = analyst_report_file_name(submission, "burn_rate")
    name_of_agency = agency_name(submission)

    with open("".join([report_path, file_name]), "w", newline="") as report_file:
        report_csv = csv.writer(report_file, delimiter=",", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        report_csv.writerow(BURN_RATE_HEADERS)

        for row in burn_rate_rows(submission):
            report_csv.writerow(
                [
                    name_of_agency,
                    row["tas"],
                    row["fiscal_year"],
                    row["period"],
                    row["program_activity_code"],
                    row["program_activity_name"],
                    row["cumulative_obligations"],
                    row["amount_available"],
                    row["straight_line_plan"],
                    row["variance"],
                    "{:.1%}".format(row["variance_pct"]) if row["variance_pct"] is not None else "",
                    row["status"],
                    burn_rate_note(row),
                ]
            )

    return file_name
