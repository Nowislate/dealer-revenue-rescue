DEALER REVENUE RESCUE: SET UP ONE PC WITH A USB PRINTER
============================================================

Print your deal paperwork when the DMS, CRM or internet is down.

This guide sets up "island mode" (decision 15).
Island mode is one Windows PC with a printer plugged straight into it.
It needs no store network and no internet.
It keeps working when the store's network is down or not safe to use.

Do this BEFORE an outage. You need the internet once, to download two files.
After that, the PC never needs the internet again.

Each step says what you should see. If you see something else, stop
and look at "If something goes wrong" at the end.


WHAT YOU NEED
-------------

1. One Windows 10 or Windows 11 PC. Use one the store can set aside.
2. A laser printer that connects with a USB cable, and its cable.
   The app sends each form to the printer as a PDF file. Pick a printer
   whose specifications list "PDF direct print" (or PDF and PostScript).
   Many office laser printers do; most home inkjet printers do not.
3. A box of plain letter paper (8.5 x 11). Every form prints on blank
   letter paper.
4. A USB stick.
5. Two files on the USB stick:
   - The Dealer Revenue Rescue release zip, named DealerRevenueRescue-<version>.zip.
   - The Python installer for Windows, from python.org
     (Downloads > Windows > "Windows installer (64-bit)"). Version 3.10
     or newer.
6. The printer's Windows driver, on the same USB stick, if the printer
   maker says it needs one. Download it from the printer maker's website now.


PART 1. INSTALL PYTHON
----------------------

Python is the free program Dealer Revenue Rescue runs on.

1. Plug in the USB stick. Double-click the Python installer.
   You should see "Install Python" with two buttons.

2. At the bottom, tick "Add python.exe to PATH".
   Then click "Install Now".
   "Install Now" includes the two parts the app needs: "tcl/tk" (the
   app's windows) and "py launcher".

3. If Windows asks "Do you want to allow this app to make changes?",
   click Yes.

4. Wait about two minutes.
   You should see "Setup was successful". Click Close.


PART 2. CONNECT THE PRINTER
---------------------------

1. Plug the printer into the PC with the USB cable. Turn the printer on.

2. Open Start > Settings > Bluetooth & devices > Printers & scanners.
   (On Windows 10: Start > Settings > Devices > Printers & scanners.)
   You should see the printer's name in the list within a minute.
   If it does not appear, run the driver installer from the USB stick,
   then check again.

3. Click the printer's name, then "Printer properties" or
   "Print a test page".
   You should get one printed Windows test page.

4. Load the paper tray with plain letter paper.


PART 3. INSTALL REVENUE RESCUE
------------------------------

1. Copy DealerRevenueRescue-<version>.zip from the USB stick to the Desktop.

2. Check the zip is the one this project published.
   Right-click Start and open "Windows PowerShell" or "Terminal". Type:

       Get-FileHash $HOME\Desktop\DealerRevenueRescue-<version>.zip -Algorithm SHA256

   You should see a long line of letters and numbers under "Hash".
   It must match the SHA-256 on the release page, every character.
   If it does not match, stop. Delete the zip and download it again.
   This is the only check that the zip came from this project. The
   installer checks that no file is damaged, not where the zip came from.

3. Right-click the zip and choose "Extract All", then click Extract.
   A folder named DealerRevenueRescue-<version> opens.
   It holds INSTALL.cmd, this guide (READ-ME-FIRST.txt) and a few folders.

4. Double-click INSTALL.cmd.
   If Windows shows "Windows protected your PC", click "More info",
   then "Run anyway". The file is a plain text script; nothing is downloaded.

5. A black window shows six steps. Each one ends with "OK".
   During the installer's step 3, small windows may flash open and close. That is the
   app testing itself on this PC. It takes one to two minutes.
   You should see "DONE. Dealer Revenue Rescue <version> is installed."

6. Press any key to close the black window.
   You should now see a "Dealer Revenue Rescue" icon on the Desktop.

The app now lives in C:\DealerRevenueRescue:
- C:\DealerRevenueRescue\app holds the program.
- C:\DealerRevenueRescue\store holds the store's data: setup, users and deals.
- "Start Dealer Revenue Rescue.cmd" opens the app, like the Desktop icon.
- "Back up now.cmd" makes a backup (Part 5).

You can delete the zip and the extracted folder from the Desktop now.
Keep the zip on the USB stick.


PART 4. FIRST-TIME SETUP IN THE APP
-----------------------------------

A manager does this once. Have these ready:
- The store's fees.
- The county surtax rates from the current Florida form DR-15DSS.
- One recent finance deal open in the DMS (and one lease, if the store
  leases), with its tax, amount financed and payment showing.

1. Double-click the "Dealer Revenue Rescue" icon.
   You should see a sign-in window asking to create the first admin.

2. Create the admin account: name, username and PIN.
   Then read the notice and click "I accept".

3. Open "Accounts / PIN". Create a second admin account.
   One lost PIN then never locks the store out.
   Setup asks for this before it lets you finish.

4. Click Setup. The setup steps open.

5. Step 0.1 asks about the network.
   Type your name next to "Your name", then click
   "This computer runs by itself".
   You should see "Standalone (island mode), confirmed by <your name>".
   Click Next.

6. Step 0.2: enter the dealership's own details and fees. Click Next.

7. Step 0.3: pick the USB printer from the list. It shows the same name
   Windows showed in Part 2. Click Next.

8. Step 0.4: assign that printer and its tray to letter paper. Click Next.

9. Step 0.5: confirm letter paper is loaded and run the test print.
   Check the page printed fully and is easy to read.
   Type your name and role to confirm. Click Next.

10. Step 0.6: pick where the store is. A Florida store picks "Florida
    (built-in rules)". A store in another state picks "Another state"
    and answers each question from its DMS setup or its state's tax
    office. Click Next.

11. Step 0.7: type the local tax rates, one place per line, as the
    name, a comma, and the rate in percent. Example:
    Pinellas, 1.5
    A Florida store types its county surtax rates from the current
    DR-15DSS. Put your own place first, then every place your buyers
    usually register in. Answer whether the store writes leases. If
    yes, type the lessor and the lease title holder. Click Next.

12. Step 0.8: check the math. Type in the recent finance deal from the
    DMS, then the tax, amount financed and payment the DMS shows. Type no
    customer name, VIN or stock number. Click "Check the math".
    You should see three lines, each marked MATCH, and "Every figure
    matches your DMS to the cent."
    If a line says DIFFERENT: check what you typed, the fees from
    Step 0.2 and the county rate. If the app is right and the DMS
    printout is the odd one out, a manager types their name and why,
    then clicks "Record why". If the store leases, do the same for the
    lease. A store that typed its own rules checks two retail deals, one
    with no trade and one with a trade, and every figure must match:
    there is no "Record why". Click Next.

13. Step 0.9: review the setup. Approve it by name. Finish.
    The home screen now shows "New retail deal" and "New lease deal".

14. Open "Accounts / PIN" again. Create an account for each person
    who will use the app.


PART 5. KEEP IT READY
---------------------

1. Take the PC off the network.
   Unplug the network cable. Turn off Wi-Fi
   (Start > Settings > Network & internet > Wi-Fi > Off).
   The app keeps working. It never needs the network.

2. Use this PC only for Dealer Revenue Rescue.
   No email, no web browsing. A clean PC is what keeps it safe
   when the rest of the store is not.

3. Write it into the store's outage plan: which PC it is, where it
   sits, and who knows the admin PINs.

4. Back up after setup, and after every day the app is used.
   Open C:\DealerRevenueRescue and double-click "Back up now.cmd".
   You should see "Backup verified: every file matches its manifest",
   and a folder opens with the new backup zip.
   Copy that zip to the USB stick. Keep the stick somewhere else.

5. Once a month, run a practice drill.
   Open Dealer Revenue Rescue, sign in and click "Practice drill".
   A practice deal opens with SAMPLE data already filled in.
   Walk through it, then answer Yes to print the practice pack.
   Every page prints with SAMPLE across it, so it can never pass as real.
   The home screen then shows "Last practice drill" with the date and
   who ran it. Staff who know the steps work faster on the day it matters.


INSTALLING A NEWER VERSION
--------------------------

1. Put the new release zip on the PC, extract it, and double-click
   INSTALL.cmd, as in Part 3.
2. The installer backs up the store's data first, into
   C:\DealerRevenueRescue\store\backups.
3. It replaces the program only. Setup, users and deals stay as they were.
4. The program you had before is kept in C:\DealerRevenueRescue\app-previous.


IF SOMETHING GOES WRONG
-----------------------

"Python 3 was not found on this PC."
  Do Part 1 again. Make sure "py launcher" was installed.

"Python's Tcl/Tk part is missing or broken"
  Run the Python installer again. Choose Modify. Tick "tcl/tk and IDLE".
  Click Next, then Install. Run INSTALL.cmd again.

"The release copy is not intact"
  The zip was damaged while copying, or a file was added to the
  extracted folder ("Not part of the release"). Delete the extracted
  folder. Copy the zip from the USB stick again, check its SHA-256,
  extract it to a new folder, and run INSTALL.cmd again.

"The test suite failed on this PC"
  Nothing was installed. Take a photo of the black window and send it
  to whoever gave you the release.

The printer is not in the list at Step 0.3.
  Close the app. Check the printer shows in Windows (Part 2, step 2).
  Open the app again and click "Refresh list".

The Desktop icon does nothing, or shows "could not start".
  Open C:\DealerRevenueRescue\store\logs\launch-error.txt.
  Send that file to whoever installed the app.

Pages print shrunk or cut off.
  Print at "Actual size" (100%), never "Fit to page".
  Use the alignment setting in the print window to nudge the fields.
