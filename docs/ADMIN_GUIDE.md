# Dealer Revenue Rescue — administrator guide

Print your deal paperwork when the DMS, CRM or internet is down.

Current as of November 24, 2026 (version 1.0.1). This one guide replaces
the earlier versioned guides (`ADMIN_GUIDE-native-safety-v1*.md` and
`ADMIN_GUIDE-native-form-images-v1*.md`); git history keeps them.

For a first install on one PC with a USB printer, follow
[SETUP-ONE-PC.md](SETUP-ONE-PC.md) (it ships in the release zip as
`READ-ME-FIRST.txt`). This guide covers what the store's admin and IT
team need after that: accounts, forms, packaging, recovery and limits.

The app is dealer-neutral, offline and holds no licensed forms. Python
standard library only. This guide describes the native Windows app.

## Install, launch and update

Build a release with `python scripts/build_windows_release.py`. It writes
`dist/DealerRevenueRescue-<version>.zip`. On the store PC, extract it and
double-click `INSTALL.cmd`. The installer checks Python and Tcl/Tk,
checks every file against `release-manifest.json`, runs the test suite,
backs up existing store data, then installs:

- `C:\DealerRevenueRescue\app` — the program, replaced on every install.
- `C:\DealerRevenueRescue\app-previous` — the program before the last install.
- `C:\DealerRevenueRescue\store` — the store's data (`config\`, `data\`,
  `backups\`, `logs\`). An install never changes it.
- `Start Dealer Revenue Rescue.cmd`, `Back up now.cmd` and a desktop shortcut.

Options: `INSTALL.cmd --target D:\SomeFolder` installs elsewhere;
`--skip-tests` skips the test run (for a reinstall of a release already
tested on that PC; `app\install.json` records `"tests_run_before_install":
false`); `--no-shortcut` skips the desktop icon.

The installer's file check compares each file with `release-manifest.json`,
which ships inside the zip. It catches a damaged copy. It cannot show where
the zip came from. Check the zip's SHA-256 against the release page before
you unzip it: `Get-FileHash .\DealerRevenueRescue-<version>.zip -Algorithm SHA256`.

The launcher (`app\launch.pyw`) always uses the `store` folder as the
app's working folder. If the app fails to start, the reason is written to
`store\logs\launch-error.txt`. `launch.pyw --check` (run with `python.exe`)
prints the version and data folder without opening a window.

Developers can still run from a checkout: set `PYTHONPATH` to `src` and
run `python -m dealerpaperwork`. The older setup, deal, document and print
module launchers all enter the same sign-in gate. The working folder
holds `config/` and `data/`; keep using the same one.

To update, run the new release's `INSTALL.cmd`. To roll back, restore
software and data together: copy `app-previous` back over `app`, and
restore the backup made before the install. Older software may not read
data written by newer software.

## Store network or one computer

Step 0.1 of setup offers two choices. **Run network check** confirms the
PC has a store network address. **This computer runs by itself** records,
by the named person, that the PC runs standalone with a directly
connected printer (decision 15, island mode). Island mode needs no
network at all. Printing goes through the Windows spooler either way, so
a USB printer and a network printer behave the same in the app.

## Tax rules, tax rates and the math check

Step 0.6 picks the store's tax and tag rules (decision 22). Florida's
rules are built in and cannot be edited. A store in another US state
answers the rule sheet: what tax the state charges on a vehicle sale,
whose address picks the local rate, any limit on the local tax or on the
whole tax, the trade-in credit (full, none, or up to a dollar limit),
whether the rebate comes off before tax, which charges are taxed, and
how a lease is taxed. "None of these fit" on the first or the lease
question stops setup: the app cannot run that state yet. The sheet is
saved in the preset (`state_rules`), and the worksheet names each line
by how the sheet treats it ("Local tax", "Trade-in credit against tax").

Step 0.7 takes the store's local rates. A Florida store types its county surtax rates from the current Florida
DR-15DSS, typed as percents, one county per line (`Pinellas, 1.5`). A
buyer from a county not on the list cannot be certified until a manager
adds it. The same step asks whether the store writes leases; a store
that does enters the lessor and the lease title holder, which every
lease deal needs.

Step 0.8 proves the math (decision 21). A manager types in one recent
finance deal from the store's DMS and the DMS's own total sales tax,
amount financed and monthly payment. The app runs it through the same
code a real deal uses, with the store's own fees and rates, and shows
each figure as MATCH or DIFFERENT, to the cent. A store that leases
checks one lease too (base payment, monthly tax, monthly payment).
Setup will not finish until every figure matches, or a named manager
records why not in a full sentence. That record goes to the audit log as
`setup_math_check_override` and into `config\setup.json`; it is tied to
the exact figures, so changing a fee, a rate or the entry re-opens the
check. A store with typed rules checks two retail deals, one with no
trade and one with a trade, plus a lease if it leases, and every figure
must match: no override (decision 22). Changing a rule re-opens the
check. The setup file keeps the check deal's numbers and the line-by-line
result. It never holds a customer name, VIN or stock number, and it
stays on the PC.

## Sign-in and the notice

Fresh install: create the first admin (username, display name,
PIN/password entered twice), read the fixed versioned notice, accept as
that admin, then complete Step 0. Bootstrap cannot configure printers or
presets or open deals. Acceptance is the first audit entry on a fresh
install; the bootstrap creation is recorded after it with the original
creation time (decision 18). If the app closes between bootstrap and
acceptance, sign in and accept on the next launch. Existing audit history
is kept.

Existing accounts sign in before the notice gate. Each account accepts
each notice version once; a new version asks each user again. Acceptance
does not rewrite saved deals. The notice is app code, not a dealer preset.
Its text is the owner's draft; counsel must review it before public
release.

The home screen opens only after acceptance. Incomplete setup routes to
Step 0. Work windows are modal: close the work window before signing out
or changing users.

## Accounts and recovery

Open **Accounts / PIN** from home, or **Create / manage accounts** in
Step 0. Create at least two admins. Every new account confirms its
PIN/password twice; a mismatch creates nothing. Each new user accepts the
current notice at their first sign-in.

An admin may reset any account's secret, including another admin's. The
old secret stops working; role, name, identity and acceptances stay.
Account file operations run under an OS file lock, so an open app cannot
restore a revoked secret. Secrets are salted PBKDF2 hashes; audit events
(`pin_reset`, `pin_changed`) never contain them. Any signed-in user can
change their own secret by entering the current one.

Finishing Step 0 with exactly one admin requires that admin's named
acknowledgment of the recovery risk. A manager cannot acknowledge for an
admin.

If every admin is locked out: dealer IT closes the app, takes a backup,
and renames `data\users.json` (preferred) or deletes it. Relaunch,
bootstrap a new admin, accept the notice, and recreate accounts under
their old usernames. Setup, deals, document library, reconciliation and
audit files are separate and stay. Keep the old users file under IT's
control. A malformed account file fails closed and calls for IT recovery.

## Dealer forms (document library)

Forms print as full-page images on blank letter paper with deal values on
top (decision 16). There is no PDF merge and no pre-printed stock.

### The demo form

**Add the demo title form** in the Documents window adds the blank Florida
title application (HSMV 82040, Rev. 07/23, from flhsmv.gov) as a draft,
with 31 deal values already placed on its two pages. Use it for the first
test print ([PRINT-TEST.md](PRINT-TEST.md)). It still needs a manager's
approval before it joins a pack, and any value can be moved with
click-to-place first. Before filing a real title application, check the
current revision of the form with your tax collector.

### Prepare a form

Get the dealer's approved blank form or template. Outside the app, export
whole pages as grayscale or RGB JPEGs at 300 DPI, upright portrait
letter: exactly 2550 × 3300 pixels. Keep the original outside the app.
The app does no PDF conversion, merging, OCR or recompression. Each page
is limited to 32 MiB. Baseline and progressive JPEGs work; CMYK/YCCK,
wrong sizes, rotation metadata and damaged files are refused with
re-export guidance.

### Add and place fields

Open **Documents**, select **Add JPEG pages**, check the page numbers and
use Move up/down or Remove before saving. Selection order is print order.
Choose a unique document ID, title, and when it prints (retail new,
retail used, lease). Built-in IDs, including `paper_packaging_checklist`,
are reserved.

Place values by clicking on the form (decision 9). Select the document,
**Reload selected form**, then **Place fields on the form...**. Pick a page
and a field, then click where the value's top-left corner belongs. Click a
placed value to select it; drag it or use the arrow keys (Shift moves
0.1 mm); Delete removes it; type a font size and press Enter. Every change
saves at once and returns the form to draft. The on-screen page is a
reduced preview (about 0.7 mm per pixel); judge final position on the
SAMPLE print.

The typed page/X/Y/font controls stay for exact entry, in millimetres
from the top left. The field list holds 87 values from the deal
(`FIELD_CATALOG` in `src/dealerpaperwork/doc_library.py`). Every page prints,
including pages with no values. The app never invents form-specific
calculations or legal wording.

### Product contracts (service contract, GAP, tire and wheel)

A form that places any **Product** field (this product's price, term,
coverage or contract number) is a product contract. It prints only in a
deal that sold that product. The retail deal screen (Step 3, under the
money) and the lease worksheet list every approved product contract. Type
a price to mark it sold; leave it blank when it was not sold. Each
contract then prints with its own price, term, coverage and contract
number. On a retail deal, the sold products must add up to the
**Optional products** amount before the deal can be certified.

Forms with separate city, state and ZIP boxes for the dealer or the
lienholder use the dealer and lienholder address parts. The deal screen
takes the lender's street, city, state and ZIP separately.

### Inspect and approve

**Save SAMPLE PDF** draws the form image under the values and a SAMPLE
mark. Print every page on blank letter paper at Actual size (100%). Check
order, orientation, small type, lines, margins, values and copies. A
failed SAMPLE export keeps the previous output.

A manager or admin approves the reviewed form. Approval and edits are
bound to the exact record on screen: page bytes, manifest, mapping and
conditions. If the form changed since it was opened, the app refuses and
asks you to **Reload selected form**, inspect it and its SAMPLE again,
then approve. Replacing or reordering images, or editing placements,
returns the form to draft and clears approval. Replacements keep
placement coordinates; they do not guess where moved content belongs.
Old image revisions stay stored for recovery. Sales users cannot approve.

### Two people editing forms at once

Every library change (add, replace pages, edit placements, approve) runs
under one file lock on the library index. A second window or app copy
that saves at the same moment waits for the first, then works from the
saved result. It never erases the other change. If both edited the same
form, the second is refused with "this document changed since it was
opened" and must reload and review. If the lock is held for more than a
few seconds on Windows, the app says another window is saving; try again.

## Printing and paper packaging

Choose a saved deal on home and open **Print / package selected**. A
retail deal records cash/finance and lender name; changing them clears
certification. Leases use their own lease record and lessor.

Approved library forms join their matching retail or lease pack before
the generated **Paper Deal Packaging Checklist**, which always prints
last, one copy, for the jacket. It is guidance, not a licensed form.
Built-in text summaries never count as an uploaded legal form. Configure
the real jacket inventory deliberately to avoid duplicate forms.

In **Packaging checks**, inspect the actual signed paper before checking a
line. The list follows the deal: used Buyers Guide or new Lemon Law, trade
payoff, assigned rebate, each library form, lease payment checks, buyer's
copies, privacy delivery, secure original form numbers, second review,
F&I sign-off and assembly sign-off. Secure-stock items are pulled by
hand; the app never prints a substitute.

When the app has no mapped form for a required line, a named user may
record a separately supplied paper form (decision 19). The app records
actor, time, checklist revision and evidence. F&I sign-off needs a
certifying role. Printing alone never completes a pack; every required
check must be recorded on a current, certified deal. Draft and drill pages
keep their marks and never become production packages.

Void-and-reprint raises the checklist revision, keeps earlier sign-offs
in history, clears current ones, and needs the replacement checklist last.
A spoiled figure means stop, void, fix, recertify and reprint; no
white-out.

Changing a saved deal, form approval, mapping or image blocks a held
package. Close it, re-review, recertify or reapprove, and reopen. A
changed package gets a new checklist revision. A required form that goes
missing or unapproved stays a blocking item. An unchanged package keeps
its jobs, print counts, checks and completion across updates; ordinary
resume never repeats completed jobs.

Missing or damaged image pages fail before anything is sent and record a
failed document. A successful job records its image revision and each
page digest; these describe bytes sent to the spooler, not proof of
printed paper.

An older print window must close and reopen after another copy changes
the same package; stale actions are refused before printing or checking.
A lower-role user cannot restore a voided F&I sign-off from an older
window. Print-session exports are immutable; choose a new file name each
time. Reconciliation saves serialize across app copies; if a save fails,
the deal stays unresolved.

Existing library conflicts with built-in IDs block output and keep files.
Return the form to draft, re-add it under a unique ID, and get manager
approval. Never delete an original form or its history to clear it.

## After the outage: reconciliation

The home screen lists unreconciled outage deals. Enter the DMS exactly as
signed, compare to the certified worksheet line by line, resolve
differences with a manager, file per store policy, then **Mark
reconciled**. It needs the current completed paper package and records
the named actor and time. Reconciliation never gates delivery during the
outage. An edited package returns the deal to the list. Drills are
excluded. Unreadable deals or sessions show as IT-recovery items.

## Practice drills

**Practice drill** on the home screen runs a whole practice deal. It opens
the normal walk-through on a SAMPLE buyer, vehicle and trade, with the
sample amounts filled in ($35,000 price, $10,000 trade). The user may
change it and certify it for practice. Then the app offers to print the
practice pack. Every page prints SAMPLE, certified or not, and a drill
never appears in the deal list or the reconciliation count.

Each drill is recorded in `store\data\drill_runs.json` (who, when, how
long, and whether the whole pack printed). The home screen shows the last
one. When the store's approved fees equal the sample fees ($1,199 dealer
fee, $199 electronic tag fee) and a county is set at 1.5%, the drill also
re-checks the tax math against the known total ($1,658.88) and says so.
With the store's own fees that check does not apply, and the home screen
does not call it a failure.

## Backup and restore

`Back up now.cmd` (or `python -m dealerpaperwork.backup create`) writes one
zip with a SHA-256 manifest: setup, print config, accounts, audit,
acceptances, bootstrap record, reconciliation, deals, print sessions and
the whole document library (current and old image revisions, retained
legacy files). Copy it to a USB stick kept away from the PC. Restore
verifies first, refuses a damaged zip, and moves current data aside to a
`.pre-restore-<timestamp>` folder. `data\installation.json` is local and
not backed up; same-install restores keep notice acceptances, a new
install asks users to accept again.

Older libraries: `stage5-1` records load as **Values only** without
rewriting the index; their PDF bytes are never printed. Use **Replace selected document's
page images** to upgrade each one, then reapprove. New writes use `stage7-1`
records and immutable image folders. Unknown or damaged schemas fail
closed.

## Limits

No real form or customer records are in this repository (decision 17).
Still open: mapping and approving the dealer's real forms; on-site Windows
UI, printer, tray, copy and physical output checks; the RAW spooler path
needs a printer that understands the PDF it receives; one-pay lease;
decision 10's unverified Florida items (surtax base under $5,000, plate
transfer fee; the store enters current-year DR-15DSS rates at Step 0.7
and proves them at Step 0.8); any other state's rules, which the store
types and proves at setup and the app never checks against that state's
law; counsel's review of the
notice. Automated tests and synthetic output do not establish those.
This build makes no production-jacket or legal certification claim.
