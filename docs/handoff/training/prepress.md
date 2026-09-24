# Claude for prepress

**What it's for:**
- triage of checkpoints
- plain-language report explanations
- file-vs-ticket checks before plating
- the auto-fix plan
- the morning digest
- if you have write access: routing and job actions

## Everyday requests

| You want to... | Ask Claude |
|---|---|
| Start the day | "Morning digest", or the prompt *Prepress triage* |
| See what's waiting | "What's waiting in checkpoints, oldest first? Which can we fix without the customer?" |
| Understand a report | "Explain the preflight report for job X for prepress." |
| Plan fixes | "What can the auto-fix branch handle on job X?" |
| Check a file against the ticket | "Compare `Incoming/job.pdf` to Pace job 123456." |
| See what's in a PDF | "What's in this PDF: sizes, bleed, inks, spot colors?" |
| Find errors | "What errors has Switch logged in the last 12 hours, grouped by flow?" |

## Write actions (named users only)

With write access, Claude can:
- route checkpoint jobs
- submit files to submit points
- replace a job's file
- lock or unlock jobs
- rush jobs

Before any of these, Claude shows what it will do. **Confirm only when it's exactly right.**
- If someone else moved the job in the meantime, Switch refuses the route. That's intentional.
- Every action is logged in Switch under the connector's user, and in the audit log.
- To undo a route, use Switch Designer as usual.

## The auto-fix branch

- The auto-fix map lists the problems the Switch auto-fix branch fixes reliably, for example RGB → CMYK
  and hairlines.
- `plan_autofixes` splits a job's problems three ways: **auto-fixable**, **prepress by hand**, and
  **needs the customer**.
- When automatic routing is on, only jobs where **every** open problem is auto-fixable are routed without a
  person. Jobs needing the customer are never auto-routed.
- Tell the prepress lead if a category shouldn't be on the list.

## Tuning

If a problem is explained wrongly, or has the wrong owner (customer vs. us), report it with the job
number. Wording and owners are set in one place and can be changed quickly.

## Reporting problems

Use the Asana form or the Teams channel. Give the job number, the request, what happened, and what you
expected.
