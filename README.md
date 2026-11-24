# Dealer Revenue Rescue

**Print your deal paperwork when the DMS, CRM or internet is down.**

When ransomware or a vendor outage takes the DMS offline, a dealership
still has buyers at the desk. Dealer Revenue Rescue is a small Windows app the
store sets up ahead of time on one PC. On the bad day, staff walk each
retail or lease deal through it, a manager certifies the numbers, and the
store prints the deal on its own forms and blank letter paper. When the
DMS comes back, the app lists every outage deal until someone enters it in
the DMS and marks it reconciled.

- **No AI.** The tax and payment math is fixed code with tests. Florida's
  rules are built in; a store in another state types its own, and setup
  proves them against its DMS. Nothing guesses.
- **No internet connection.** The app never connects to anything. It
  runs on a PC with the network cable unplugged and Wi-Fi off.
- **Your data stays on that PC.** Deals, users and setup live in one
  folder. Backups are zip files you copy to a USB stick.
- **Your forms, your fees.** A fresh install holds no dealer name, fee
  or rate. The store enters its own during setup, and a manager approves
  them by name.

It is free and open source under the Apache 2.0 licence. A dealer's IT
person can read every line and confirm the points above.

## Who it is for

- **The dealer principal, GM or controller** who wants a written plan for
  delivering cars during an outage.
- **The store's IT person**, who installs it, keeps the PC clean and runs
  the backups. This README is written for you.

## What it does

1. **Setup, once, before an outage.** Store details, fees, the printer
   and its tray, the state's tax and tag rules, and the local tax rates.
   A Florida store picks the built-in Florida rules and types its county
   surtax rates from the current DR-15DSS. A store in any other US state
   answers twelve plain questions about how its state taxes a deal. A
   manager approves the set by name. Nothing runs on unapproved values.
2. **A math check against your own DMS.** During setup a manager types in
   one recent finance deal from the store's DMS, and the tax, amount
   financed and payment the DMS showed. The app shows each figure side
   by side, matching or different, to the cent. Setup does not finish
   until they match, or a named manager records why not. A store that
   leases checks one lease the same way. A store that typed its own
   rules checks two retail deals, one with a trade, and every figure
   must match.
3. **Retail and lease deals.** A step-by-step walk-through: buyer,
   vehicle, trade, money, worksheet. The worksheet shows every tax and
   payment line.
4. **Certification.** A named manager, F&I or admin user certifies each
   deal. Until then, every page prints DRAFT. Editing a certified deal
   clears the certification.
5. **Printing on blank letter paper.** The store adds its own blank forms
   as page images, clicks where each value goes, and a manager approves
   each form. The app draws the form and the deal's values together on
   plain letter paper. Secure-stock forms are pulled by hand and checked
   off, never printed.
6. **Practice drills.** One button runs a SAMPLE deal end to end and
   records who ran it and when. Every drill page prints SAMPLE.
7. **Reconciliation.** After recovery, each outage deal stays on the home
   screen until someone enters it in the DMS exactly as signed and marks
   it reconciled.
8. **Accounts and an audit log.** Roles for admin, manager, F&I and
   sales. Every sign-in, certification, print and void is logged.

## What it does not do

Read this list before you rely on it.

- **Florida is built in; every other state is typed by the store.** The
  app ships no other state's rules. A store outside Florida answers the
  rule sheet in setup, and setup will not finish until two of its DMS
  deals match to the cent, one with a trade and one without, plus a
  lease if it leases. No manager can override a typed-rules mismatch.
- **Some state rules do not fit the sheet.** Tax that changes with price,
  weight or vehicle type, a tax on a state-set value instead of the
  price, and stacked local taxes with different limits are not
  supported. Setup stops with a plain message instead of guessing.
- **The math check proves the deals you typed, not every deal.** Keep
  checking the worksheet against what you expect on each outage deal.
- **No one-pay (single payment) lease.** Monthly-payment leases only.
- **Some Florida items stay with the store.** The app does not hard-code
  them; the store sets or confirms each one:
  - County surtax rates and the plate fee are setup values. Enter them
    from the current DR-15DSS and the current fee schedule.
  - When a trade brings the taxable amount under $5,000, the worksheet
    shows a warning. Certification waits until a named user
    acknowledges it, and the acknowledgment is logged.
- **No forms ship with the app.** The store supplies its own blank forms
  and maps them during setup.
- **No connections.** No lender portals, credit bureaus, e-contracting,
  DMS or CRM. Nothing is sent anywhere.
- **Windows only.** Windows 10 or 11, with Python from python.org.
- **The printer must accept PDF.** Each form goes to the printer as a PDF
  file. Pick a printer whose specifications list PDF direct print (or PDF
  and PostScript). Many office laser printers do; most home inkjets do
  not.
- **Not legal or tax advice.** Have your controller check the worksheet
  against your DMS before you rely on it. See the licence and disclaimer
  below.

## Install

Full steps, with what you should see at each one:
[docs/SETUP-ONE-PC.md](docs/SETUP-ONE-PC.md). It also ships inside the
release zip as `READ-ME-FIRST.txt`. In short:

1. Install Python 3.10 or newer from python.org. Keep "tcl/tk" and
   "py launcher" ticked.
2. Download `DealerRevenueRescue-<version>.zip` from the release page. Check
   its SHA-256 against the one published with the release.
3. Extract it and double-click `INSTALL.cmd`. The installer checks every
   file against its manifest, runs the full test suite on that PC, and
   installs to `C:\DealerRevenueRescue`.
4. Open "Dealer Revenue Rescue" from the desktop. Create two admin accounts,
   accept the notice, and run Setup.
5. Unplug the network. Use that PC for nothing else.

## Back up

Double-click `C:\DealerRevenueRescue\Back up now.cmd`. It writes one zip with a
SHA-256 manifest into `C:\DealerRevenueRescue\store\backups` and checks it.
Copy the zip to a USB stick and keep the stick somewhere else. Do it after
setup and after every day the app is used.

Restore, from the install folder's `app` directory:

```
python -m dealerpaperwork.backup verify <zip>
python -m dealerpaperwork.backup restore <zip> --data-root ..\store
```

A restore checks the zip first and moves the current data aside. It never
deletes it.

## Run a drill

Once a month, sign in and click **Practice drill**. Walk the SAMPLE deal
through and print the practice pack. The home screen then shows the last
drill, who ran it, and whether the whole pack printed.

## How to check the "no internet" claim

- The code uses the Python standard library only. There are no
  third-party packages.
- The only network code is `src/dealerpaperwork/network_check.py`. It
  reads this PC's own address list for setup Step 0.1. It opens no
  connection.
- Printing goes through the Windows print spooler
  (`src/dealerpaperwork/printers.py`).
- Search the source for `urllib`, `http`, `socket` and `requests` to
  confirm.

## Where the data lives

Everything is under `C:\DealerRevenueRescue\store`:

| Path | What it holds |
|---|---|
| `config\setup.json` | Store details, fees, rates, printer setup |
| `data\users.json` | Accounts (salted password hashes, never plain PINs) |
| `data\audit.jsonl` | The append-only audit log |
| `data\deals\` | One file per deal, saved after every change |
| `data\documents\` | The store's form images and field placements |
| `data\drill_runs.json` | Practice drill history |
| `backups\` | Backup zips |

## For developers

- Python 3.10+, standard library only. Money is `decimal.Decimal`, never
  float.
- Run the tests from the repo root: `python -m unittest discover -s tests`.
- Build the Windows release zip: `python scripts/build_windows_release.py`.
- Scan a tree for real dealer or customer data before you publish:
  `python scripts/scan_public_tree.py .`
- The Python package keeps its original name, `dealerpaperwork`.
- Settled design decisions, with reasons: [docs/DECISIONS.md](docs/DECISIONS.md).
  The administrator guide: [docs/ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md).
- The acceptance fixture is a test: price $35,000 + dealer fee $1,199 −
  trade $10,000 → taxable base $26,199, state tax $1,571.94, county surtax
  (1.5% on the first $5,000) $75.00, total tax $1,646.94, exactly.

Pull requests are welcome. Never commit a real deal, stock number, store
or customer. Every fixture here is made up.

## Licence and disclaimer

Licensed under the Apache License, Version 2.0. See `LICENSE`.

This software calculates sales tax, lease payments and amounts due. It is
provided "as is", without warranty of any kind (Apache 2.0, sections 7
and 8). Tax rules, rates and fees change, and every dealer's setup is its
own. Before relying on any figure, have your controller or accountant
verify the calculations against your DMS and current law in your state. All
fixtures and sample data in this repository are made up; none describe a
real dealer, deal or customer.

Built by Now is Late, Inc. ([nowislate.com](https://nowislate.com)).
