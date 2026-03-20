# qa_testing.db — Table reference

Database path: `db/qa_testing.db` (override with `QA_DB_PATH` in .env).

## projects
| Column        | Type    | Notes        |
|---------------|---------|--------------|
| id            | INTEGER | PK, auto     |
| project_key   | TEXT    | NOT NULL     |
| project_name  | TEXT    |              |
| created_date  | TEXT    |              |

## user_stories
| Column          | Type    |
|-----------------|---------|
| id              | INTEGER | PK
| project_key     | TEXT    |
| jira_id         | TEXT    |
| title           | TEXT    |
| description     | TEXT    |
| normalized_story | TEXT    |
| created_date    | TEXT    |

## test_cases (updated)
| Column          | Type    | Notes              |
|-----------------|---------|--------------------|
| id              | INTEGER | PK                 |
| jira_id         | TEXT    |                    |
| testcase_id     | TEXT    |                    |
| title           | TEXT    |                    |
| description     | TEXT    |                    |
| preconditions   | TEXT    |                    |
| expected_result | TEXT    |                    |
| test_data       | TEXT    |                    |
| priority        | TEXT    |                    |
| status          | TEXT    | default 'NOT_RUN'  |
| created_date    | TEXT    |                    |

Steps are stored in **test_case_steps** (one row per step).

## test_case_steps (new)
| Column        | Type    | Notes    |
|---------------|---------|----------|
| id            | INTEGER | PK       |
| testcase_id   | TEXT    | NOT NULL |
| step_number   | INTEGER | NOT NULL |
| step_action   | TEXT    |          |
| expected_result | TEXT  |          |

## automation_scripts
| Column        | Type    |
|---------------|---------|
| id            | INTEGER | PK
| testcase_id   | TEXT    |
| script_name   | TEXT    |
| script_path   | TEXT    |
| framework     | TEXT    |
| created_date  | TEXT    |

## execution_results
| Column           | Type    |
|------------------|---------|
| id               | INTEGER | PK
| testcase_id      | TEXT    |
| execution_status | TEXT    |
| execution_logs   | TEXT    |
| report_path      | TEXT    |
| execution_date   | TEXT    |

## bugs
| Column      | Type    |
|-------------|---------|
| id          | INTEGER | PK
| testcase_id | TEXT    |
| jira_bug_id | TEXT    |
| bug_status  | TEXT    |
| created_date| TEXT    |
