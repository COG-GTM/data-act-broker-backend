"""Use UNION ALL in submission_updated_at_view

Revision ID: e2cbb5102de1
Revises: e10ecbfda17f
Create Date: 2026-11-20 02:30:00.000000

"""

# revision identifiers, used by Alembic.
revision = 'e2cbb5102de1'
down_revision = 'e10ecbfda17f'
branch_labels = None
depends_on = None

from alembic import op


def upgrade(engine_name):
    globals()["upgrade_%s" % engine_name]()


def downgrade(engine_name):
    globals()["downgrade_%s" % engine_name]()


def upgrade_data_broker():
    # max() ignores duplicate rows, so UNION ALL returns the same updated_at as UNION without the dedup sort
    op.execute("""
            CREATE OR REPLACE VIEW submission_updated_at_view (submission_id, updated_at) AS
            SELECT temp.submission_id, max(temp.updated_at) as updated_at
            FROM (SELECT sub.submission_id, sub.updated_at
            FROM submission as sub
            UNION ALL
            SELECT job.submission_id, job.updated_at
            FROM job, submission as sub_2
            WHERE job.submission_id=sub_2.submission_id) as temp
            GROUP BY submission_id
        """)


def downgrade_data_broker():
    op.execute("""
            CREATE OR REPLACE VIEW submission_updated_at_view (submission_id, updated_at) AS
            SELECT temp.submission_id, max(temp.updated_at) as updated_at
            FROM (SELECT sub.submission_id, sub.updated_at
            FROM submission as sub
            UNION
            SELECT job.submission_id, job.updated_at
            FROM job, submission as sub_2
            WHERE job.submission_id=sub_2.submission_id) as temp
            GROUP BY submission_id
        """)
