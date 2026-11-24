# Dealer Revenue Rescue @VERSION@

Print your deal paperwork when the DMS, CRM or internet is down.

A small Windows app a dealership sets up ahead of time on one PC. On the
bad day, staff walk each retail or lease deal through it, a manager
certifies the numbers, and the store prints the deal on its own forms and
blank letter paper. No AI. No internet connection. A fresh install holds
no dealer name, fee or rate.

## What changed in 1.1.0

- **Any printer Windows can print to.** The app now draws each page
  through the printer's own Windows driver, the way any Windows program
  prints. Before, it sent a PDF file straight to the printer, so only
  printers that read PDF themselves could print. Pre-printed form stock
  and impact printers remain a custom setup: contact Now is Late.
- **More state tax shapes.** Setup has five new optional questions for
  retail deals: an extra tax on a dollar range of the price, a state rate
  set by the selling price, vehicle classes with their own rate, a least
  tax per deal, and tax on the state's own value for the vehicle (typed
  on the deal from the state's lookup). Blank answers change nothing; an
  existing setup keeps its approval and its math check.
- **"Don't lose or forget your PIN."** The first-admin screen and the
  guides say it plainly. Re-installing does not reset a PIN; if every
  admin forgets, IT resets the accounts file (see the admin guide).
- **Support.** There is no support line. Report bugs as GitHub issues.
  Setup help is a paid service from Now is Late, Inc.

## What changed in 1.0.3

- Restoring the backup the installer makes before each install now
  works. Before, a restore from a zip inside the store folder stopped
  partway and left the app empty; the data stayed safe in the folder
  beside the store. A backup restored from a USB stick was not affected.
- The README corrects four points: one demo form ships, the installer
  runs the release's own tests, PINs have no minimum length or lockout,
  and the audit log is a plain file.

## What changed in 1.0.2

- The installer refuses any file in the unzipped folder that its
  manifest does not list, and names each one. Before, it checked only
  the listed files.
- The installer copies only the program files the manifest lists.
- The README has a section for IT approvers: what the installer runs,
  plain-text data files, unencrypted backups, the copy a restore keeps,
  printing through Windows, and no automatic update.

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
runs the release's test suite on that PC, and installs to
`C:\DealerRevenueRescue`.

## Before you rely on it

- **Any printer Windows can print to,** on plain 8.5 x 11 paper.
- **Run the practice drill on paper** before an outage. It is the store's
  own print test: every page prints SAMPLE.
- **Florida's tax rules are built in.** A store in another state answers
  the rule sheet in setup, and setup proves the answers against two of
  its own DMS deals before the app runs.
- Have your controller or accountant check the figures against your DMS.
  The software is provided "as is" under the Apache License 2.0.

Built by Now is Late, Inc. (nowislate.com).
