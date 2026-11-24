# Dealer Revenue Rescue @VERSION@

Print your deal paperwork when the DMS, CRM or internet is down.

A small Windows app a dealership sets up ahead of time on one PC. On the
bad day, staff walk each retail or lease deal through it, a manager
certifies the numbers, and the store prints the deal on its own forms and
blank letter paper. No AI. No internet connection. A fresh install holds
no dealer name, fee or rate.

## What changed in 1.0.1

- The setup network check's description now says exactly what it does:
  one lookup of this PC's own name, only when you press "Run network
  check". It opens no connection. The code did not change.
- The install guides give the PowerShell command that checks the zip's
  SHA-256. That check is the only proof the zip came from this project.
- A `NOTICE` file names the copyright holder, Now is Late, Inc.
- The installer records in `app\install.json` whether its tests ran.

## Download

`DealerRevenueRescue-@VERSION@.zip`, below.

SHA-256: `@SHA256@`

Check it before you install. In PowerShell:

    Get-FileHash .\DealerRevenueRescue-@VERSION@.zip -Algorithm SHA256

The zip is reproducible: the same commit builds the same bytes on
Windows and on Linux, and this release was refused unless both matched.

## Install

Follow `READ-ME-FIRST.txt` inside the zip (the same steps as
`docs/SETUP-ONE-PC.md`). In short: install Python 3.10 or newer, extract
the zip, double-click `INSTALL.cmd`. The installer checks every file,
runs the full test suite on that PC, and installs to
`C:\DealerRevenueRescue`.

## Before you rely on it

- **The printer must accept PDF directly** (PDF direct print). See the
  README.
- **Run the practice drill on paper** before an outage. It is the store's
  own print test: every page prints SAMPLE.
- **Florida's tax rules are built in.** A store in another state answers
  twelve questions in setup, and setup proves the answers against two of
  its own DMS deals before the app runs.
- Have your controller or accountant check the figures against your DMS.
  The software is provided "as is" under the Apache License 2.0.

Built by Now is Late, Inc. (nowislate.com).
