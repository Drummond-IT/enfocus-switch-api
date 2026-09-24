# Claude for estimating

**What it's for:**
- drafting an estimate item from a customer's PDF
- checking a request for quote (RFQ) is complete
- finding similar past jobs

**It never prices anything.** Pricing stays in Pace, done by you.

## Everyday requests

| You want to... | Ask Claude |
|---|---|
| Start from a file | "Draft an estimate item from `Incoming/ACME_booklet.pdf`." (or the prompt *Draft an estimate from a print file*) |
| Check an RFQ | Paste the email, or use the prompt *Turn an RFQ email into an estimate request* |
| Check details you typed | "Is this quote request complete: 22-page saddle-stitched booklet, 500 and 1000, 4/4?" |
| Find comparables | Similar jobs are listed with the draft when Pace is connected |

## What you get back

- **Draft item:**
  - the product guess, size, pages, sides, inks, spot colors and bleed
  - a confidence rating
  - what's still needed (quantity, stock, finishing, dates)
  - a Pace item-template payload to enter
- **RFQ check:**
  - `ready_to_quote` (yes/no)
  - the missing details
  - production problems, e.g. "saddle stitch needs a multiple of 4 pages; 22 → 24 or 20?"
  - questions ready to send to the customer

## Good habits

- Treat the draft as a starting point. Check the size, pages and inks against the file.
- Send the customer questions yourself, after editing them.
- If the required details for a product type are wrong (e.g. envelopes don't need stock asked), tell the
  estimating lead; the list is easy to change.

## Reporting problems

Use the Asana form or the Teams channel, with the file or RFQ and what was wrong.
