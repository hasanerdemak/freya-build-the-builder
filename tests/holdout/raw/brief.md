# Northgate Savings: material for the business collections line

Northgate Savings is invented for testing a builder. Nothing here describes a real bank or a real company. The material below is everything the team sent over. It is not tidy.

---

## 1. Mail from Priya (operations) to the vendor, 2 February

Hi,

you asked how the calls should go. Short version from my side, the policy extract below has the details.

We call the contact person of a small business that is behind on a business loan or a business card. The call variables you get are `customer_id`, `contact_name`, `company_name` and `now` (like `2028-06-05T09:40`, use it for every date, never the real clock).

Start with: "Hello, this is the virtual assistant of Northgate Savings, calling for {company_name}. Am I speaking with {contact_name}?" Please do not say "late", "collections" or "owed" before the caller has passed the checks, and do not name the product.

If it is not the contact person: ask when the contact person can be reached. We can book a callback for the same day or the next working day, between 08:30 and 16:00. Book it with the tool and close as `unreached` with reason `callback_booked`. If they cannot give a time in that window, do not book, close as `unreached` / `no_slot`. One ask is enough, do not go back and forth.

If the company no longer exists or the person says they have nothing to do with it, close as `unreached` / `not_our_customer`.

Best,
Priya

## 2. Policy extract BC-4 (collections, business customers), version of 12 January

**4.1 Checks.** Two questions, in this order: the company registration number (8 digits, keep leading zeros), then the year the company was founded. Use the verification tool for each answer. Three wrong answers in total end the checks. After that, give no account details, tell the caller to contact their relationship manager, and close as `unverified` with reason `checks_failed`.

**4.2 What is owed.** Once the checks are passed, load the account. Tell the caller you are calling from the business collections desk about a missed repayment. Name each product ("business loan" or "business card ending" plus the last four digits), the amount that is past due and the number of days.

**4.3 Working days.** Monday to Friday. Not working: 2028-06-12, 2028-07-03, 2028-12-25.

**4.4 Promise dates.** A promise date is a working day, not before today. The latest date is the 8th working day after today.

**4.5 Amounts.** Ask for the full past-due amount. The lowest amount we record is the `min_instalment` of the product. Between that and the full amount is fine. Below it, say we cannot record that amount and offer the lowest amount one time.

**4.6 When the caller says no.** One further proposal only: the lowest amount, on the latest date. If that is refused too, stop.

**4.7 Recording.** Read back product, amount and date and get a clear yes, for each product separately. Then record with the promise tool. If the tool fails, try one more time. If it fails again, say that the promise could not be recorded. After a recorded promise say: "We will email a confirmation to the address on file."

**4.8 Notes.** We do not use notes on this line.

**4.9 Closing.** Thank the caller, say goodbye, then close. Exactly one close per call, as the last action. Codes are in the code list.

## 3. Memo CR-19 from Risk, 20 March: changes to BC-4

Effective immediately.

(a) Section 4.4, second sentence is replaced: for products more than 60 days past due the latest date is the 3rd working day after today. For all other products the 8th working day stays.

(b) Section 4.3: add 2028-06-06 to the list of days that are not working days.

(c) Section 4.6 does not apply to products more than 60 days past due. For those there is no further proposal: after a refusal, stop.

(d) Callers who ask for a restructure of the loan are handed to the `restructure` team with the transfer tool. Close as `handed_off` with reason `restructure`. Callers who ask for a human are handed to the `desk` team, close as `handed_off` / `desk`.

## 4. Mail from Priya, 22 March, re: callback hours

Small correction to my mail of 2 February: the callback window ends at 15:30, not 16:00. The start time is unchanged.

## 5. Code list for close_call (from the system team)

| outcome | reason | meaning |
|---|---|---|
| `promise_taken` | (none) | at least one promise was recorded |
| `handed_off` | `restructure`, `desk` | the call was transferred; this wins over a recorded promise |
| `no_commitment` | `declined` | the caller would not commit |
| `no_commitment` | `claims_paid` | the caller says the amount is already paid; say it can take two working days to show and take no promise |
| `no_commitment` | `contested` | the caller says the debt is not theirs or is wrong; do not argue, take no promise |
| `no_commitment` | `record_failed` | the promise tool failed twice |
| `unverified` | `checks_failed`, `checks_refused` | the checks were failed, or the caller would not do them (explain once that they protect the company's account; a second refusal closes) |
| `unreached` | `callback_booked`, `no_slot`, `not_our_customer` | see Priya's mail |

The old reason `no_answer` is still in some exports. Do not use it.
