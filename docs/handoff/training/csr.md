# Claude for CSRs: Switch and Pace

**What it's for:**
- answering "where's my job?"
- explaining preflight problems to customers in plain English
- checking a file against the order
- keeping approvals moving

**What it can't do:**
- change anything in Switch or Pace, unless your setup allows it
- price anything
- send anything to a customer

## Setup check (once)

Open Claude Desktop and ask: **"Check the Switch connection."** You should see your Switch server and
"read-only" (or the write settings IT gave you). If you see an error, contact IT.

## Everyday requests

| You want to... | Ask Claude |
|---|---|
| Find a job | "Where is order 123456?" / "Find ACME's jobs from this week." |
| Answer a customer | "Where is order 123456 and what should I tell the customer?" (or use the prompt *CSR order status*) |
| Explain a preflight problem | "Explain the preflight report for job 123456 as a short email to the customer." |
| Get the internal view | "Explain the same report for a CSR: what do we need from them?" |
| Check a file against the order | "Does `Incoming/ACME_flyer.pdf` match the order: 8.5 x 11, 4/4, 2 pages?" or "…match Pace job 123456?" |
| Work out which job an email is about | Paste the subject or text: "Which job is this about?" |
| Take the customer's standing agreements into account | Name the customer: "Explain the report for job 123456 for ACME Corp." Issues they've accepted are marked as accepted |

## Reading the preflight verdict

| Verdict | Meaning | What you do |
|---|---|---|
| **Ready** | Prints as is | Nothing |
| **Prepress can fix** | We fix it in-house | Nothing for the customer; prepress handles it |
| **Needs customer** | We need a new file or the customer's OK | Send the customer text, after reading it |
| **Accepted per customer agreement** | Customer signed off on this issue before | Nothing; it's listed for the record |

## Proof approvals (if you have write access)

"Approve the proof for job X, approved by Jane Smith (email)." Claude shows a **plan** first:
1. Route in Switch.
2. Set the Pace status.
3. Add a Pace note.

Say "go ahead" only if the plan is right. A step marked **manual** means you do it in Pace yourself; the
instructions are shown.

## Good habits

- Always read customer text before sending it. Fix anything that doesn't sound like us.
- If a verdict looks wrong, tell prepress and report it; the wording gets tuned from your reports.
- Standing customer agreements (e.g. "accepts low-res") go to the CSR lead to add to the rules file.

## Reporting problems

Use the Asana form or the Teams channel. Give the job number, what you asked, and what was wrong.
