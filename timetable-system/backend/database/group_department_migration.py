"""Add an optional department to existing groups without rewriting their data.

Forward-only additive migration. Older code can run with the unused nullable
column retained; rollback does not require dropping it or its assigned values.
"""


def migrate_group_department(execute):
    columns = {row[1] for row in execute("PRAGMA table_info(student_groups)")}
    if "department_id" not in columns:
        execute("ALTER TABLE student_groups ADD COLUMN department_id INTEGER REFERENCES departments (id)")
