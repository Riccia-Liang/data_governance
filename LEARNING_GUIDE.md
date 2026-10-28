# Beginner's guide

This project shows how a company can review supplier data before using it. It uses Python and a small SQLite database. All people, suppliers, tax IDs and bank details are fictional.

## How is this different from cleaning and EDA?

| Work | Main question | Example |
|---|---|---|
| Data cleaning | How do I fix or standardize values? | Remove spaces and change `cn` to `CN` |
| EDA: Exploratory Data Analysis | What does the data show? | Count missing tax IDs and find possible duplicates |
| Data governance | Who sets the rules, approves changes and fixes problems? | Require a separate approver before a bank change takes effect |
| MDM: Master Data Management | Which supplier record should different systems use? | Link two source IDs to one master supplier ID |

These activities work together. Cleaning fixes values. EDA helps you understand the data. Governance adds rules, people and a record of decisions.

For example, a bank account can have the right format but still be an unauthorized change. A format check alone cannot tell you who approved it.

## Run the project

1. On GitHub, select **Code → Download ZIP** and unzip the files.
2. Open a terminal in that folder.
3. Run `python3 demo.py`. On Windows, you can use `py demo.py`.
4. Open the `runs/.../report.md` file shown in the terminal.
5. Run `python3 -m unittest -v`. All 16 tests should pass.

No extra Python packages are needed. Use Python 3.9 or later with SQLite JSON1 support. Each run creates a new results folder and keeps the previous run.

## Follow one story

### 1. Keep the original records

Open `source_suppliers.csv`. There are eight rows from two fictional systems, ERP_A and ERP_B. They describe four suppliers.

Some rows have spaces or different letter cases. One has no tax ID. Another uses NET90, which is outside this demo's allowed payment terms.

The program keeps the raw values. It does not guess missing information.

### 2. Check the data

The program checks required fields, formats, payment terms and duplicate identities. See `validation_rules.csv` for the rules.

An exact country and tax ID match creates a duplicate candidate. A data steward must review the match. The program does not merge records on its own.

### 3. Submit a request

A request records the proposed values, the current values, the requester and the reason. Once submitted, its contents cannot be edited. A correction needs a new request, so the old one remains available for review.

### 4. Get approval

Bob, the data steward, checks the request. Carol or Diana reviews it. Only an approved request can update the master supplier record.

In the bank-change example, Carol submits the request. She cannot approve it herself, even though she has an approver role. Diana must approve it.

The program also checks the record version before applying a change. An old request cannot overwrite a newer update.

### 5. Check the ERP copy

The master record may be updated while the ERP copy still contains the old value. SQL checks compare all supplier fields and the version number.

The demo first simulates a failed sync. The difference remains visible. A successful retry brings the records back into agreement.

The ERP here is just a local database table, not a real business system.

### 6. Close the issue

A failed check opens an issue. The owner records the cause and the fix. A corrected request goes through approval. A separate reviewer then checks the current master and ERP data before closing the issue.

The issue follows these steps:

`OPEN → INVESTIGATING → REMEDIATED → CLOSED`

A clean value alone does not close the issue. The project also needs a record of how it was fixed and checked.

## What should I read first?

| File | What to look for |
|---|---|
| `SAMPLE_RESULTS.md` | What happened in the completed demo |
| `source_suppliers.csv` | The original problems |
| `demo.py` | The story, step by step |
| `validation_rules.csv` | What each check means |
| `risk_control_matrix.csv` | The risk, action, owner and evidence |
| `schema.sql` | Where each record is stored |
| `governance.py` | How submit, validate, approve and apply work |
| `test_governance.py` | How invalid actions are tested |

## Read the output

- `requests.csv`: who submitted, checked and approved each change.
- `validation_results.csv`: which rules passed or failed.
- `issues.csv`: the cause, fix, replacement request and reviewer.
- `audit_events.csv`: successful workflow actions and before/after values.
- `source_mapping.csv`: which source records belong to each master supplier.
- `results.json`: blocked attempts and monitoring results at different stages.

The same input produces the same business counts. Timestamps change on each run.

## Three small exercises

1. Change the new bank token in `demo.py` from `DEMO-9001` to `DEMO-9002`. Run the demo again and compare the change records.
2. Read `test_stale_approved_update_cannot_overwrite_newer_master`. Explain why an approved request can still be blocked.
3. Pick one row in `risk_control_matrix.csv`. Explain the risk, who acts and what record they leave.

The demo expects its sample data. Changing the input may cause an assertion to fail. For a different scenario, add a separate test or script and state the new expected result.

## Explain it in an interview

First run the project, read the code and try the exercises. Then describe only what you understand. You can adapt this introduction:

> I developed a small AI-assisted supplier data governance project with Python and SQLite. It checks supplier data, requires independent approval, records changes and compares the master record with a simulated ERP. I used synthetic data to learn how data quality checks fit into a business process. This is a learning project, not production SAP or SOX experience.

Be ready to answer:

1. Why did you keep the original records?
2. Why did you review duplicate candidates instead of merging all of them?
3. Why can Carol not approve her own bank change?
4. What happens if the ERP sync fails?
5. What must happen before an issue can be closed?

## Know the limits

The actor names are simulated, not real logins. The evidence references are labels, not verified documents. The local database owner can bypass the code or remove history-protection triggers.

This project does not prove SOX compliance or experience with SAP. A real system would also need secure login, managed permissions, protected evidence, real system connections and ongoing monitoring.
