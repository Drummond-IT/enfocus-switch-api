"""Print-shop automations built on the Switch connector.

Modules:
    specs          JobSpec: a normalized job ticket (from Pace, a person, or an RFQ)
    pdf_facts      What a print PDF actually contains (sizes, inks, spot colors, per page)
    ticket_check   Compare a PDF against its job ticket
    estimate_draft Draft an item spec / Pace item template from a PDF
    autofix        Map preflight issues to auto-fix routes / PitStop Action Lists
    job_matching   Find job numbers in file names and email text
    pace           Pace gateway interface (read-only Postgres + write stub) and a fake
    workflows      Multi-system workflows (proof approval) with dry-run plans
    digest         Morning digest and stuck-job alerts
"""
