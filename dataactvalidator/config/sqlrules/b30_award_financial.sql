-- Award obligations reported in File C (award financial) must be backed by a row in File B (object class program
-- activity) for the same TAS, object class, and program activity code+name in the same submission period. Only rows
-- with a non-zero TransactionObligatedAmount are checked, so balance-only rows (no obligation) are not flagged. The
-- unmatched TransactionObligatedAmount is reported as the difference so the amount of unbacked obligation is visible.
WITH award_financial_b30_{0} AS
    (SELECT row_number,
        submission_id,
        account_num,
        object_class,
        program_activity_code,
        program_activity_name,
        transaction_obligated_amou,
        display_tas
    FROM award_financial
    WHERE submission_id = {0}
        AND COALESCE(transaction_obligated_amou, 0) <> 0
        AND (COALESCE(program_activity_code, '') <> ''
            OR COALESCE(program_activity_name, '') <> '')),
ocpa_b30_{0} AS
    (SELECT account_num,
        object_class,
        program_activity_code,
        program_activity_name
    FROM object_class_program_activity
    WHERE submission_id = {0}
        AND (COALESCE(program_activity_code, '') <> ''
            OR COALESCE(program_activity_name, '') <> ''))
SELECT
    af.row_number AS "source_row_number",
    af.display_tas AS "source_value_tas",
    af.object_class AS "source_value_object_class",
    af.program_activity_code AS "source_value_program_activity_code",
    af.program_activity_name AS "source_value_program_activity_name",
    af.transaction_obligated_amou AS "source_value_transaction_obligated_amount",
    af.transaction_obligated_amou AS "difference",
    af.display_tas AS "uniqueid_TAS",
    af.object_class AS "uniqueid_ObjectClass",
    af.program_activity_code AS "uniqueid_ProgramActivityCode",
    af.program_activity_name AS "uniqueid_ProgramActivityName"
FROM award_financial_b30_{0} AS af
JOIN submission AS sub
    ON sub.submission_id = af.submission_id
WHERE NOT EXISTS (
        SELECT 1
        FROM ocpa_b30_{0} AS op
        WHERE COALESCE(af.account_num, 0) = COALESCE(op.account_num, 0)
            AND (COALESCE(af.program_activity_code, '') = COALESCE(op.program_activity_code, '')
                OR COALESCE(af.program_activity_code, '') = ''
                OR af.program_activity_code = '0000'
            )
            AND (UPPER(COALESCE(af.program_activity_name, '')) = UPPER(COALESCE(op.program_activity_name, ''))
                OR COALESCE(af.program_activity_name, '') = ''
            )
            AND (COALESCE(af.object_class, '') = COALESCE(op.object_class, '')
                OR (af.object_class IN ('0', '00', '000', '0000')
                    AND op.object_class IN ('0', '00', '000', '0000')
                )
            )
    )
    AND NOT (UPPER(af.program_activity_code) = 'OPTN'
        AND UPPER(af.program_activity_name) = 'FIELD IS OPTIONAL PRIOR TO FY21'
        AND sub.reporting_fiscal_year < 2021);
