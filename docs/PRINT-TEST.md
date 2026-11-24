# Print test: one PC, one printer, about one hour

Dealer Revenue Rescue — print your deal paperwork when the DMS, CRM or internet is down.

This test proves the app installs on a real Windows PC and prints a real
form, aligned, on a real printer. The photos at the end are the evidence.

## What you need

1. A Windows 10 or 11 PC you may install software on.
2. A **laser printer**, connected by USB, with its Windows driver
   installed. The app draws each page through that driver, so any
   printer Windows can print to works.
3. Twenty sheets of plain letter paper.
4. A USB stick holding the release zip (`DealerRevenueRescue-1.2.0.zip`) and
   the Python 3 Windows installer from python.org.
5. A phone camera.

## Steps

1. **Install.** Follow [SETUP-ONE-PC.md](SETUP-ONE-PC.md), Parts 1 to 3.
   You should see "DONE. Dealer Revenue Rescue 1.2.0 is installed." Photograph
   that window.
2. **Set up with the sample amounts.** Follow Part 4. At Step 0.2, use
   a dealer fee of 1,199 and an electronic tag fee of 199. At Step 0.6,
   pick Florida. At Step 0.7, type `Sample County, 1.5` and answer No to
   leases. These match the built-in drill, so the drill re-checks the
   tax math. At Step 0.8,
   type this sample deal: county Sample County, selling price 16250,
   accessories 85, trade allowance 4500, trade payoff 2100, title and
   state fees 146.85, products 895, cash down 500, term 72, APR 7.49,
   days to first payment 41. For the DMS figures type tax 868.98,
   amount financed 16743.83, payment 290.08. Click **Check the math**.
   You should see three MATCH lines. Photograph that screen. Then change
   the DMS payment to 290.09 and click **Check the math** again: the
   payment line should say DIFFERENT, and Next should refuse. Put it
   back to 290.08 before you go on.
3. **Add the demo form.** On the home screen, click Documents, then
   **Add the demo title form**. Select it in the list. Click
   **Save SAMPLE PDF...**, open the PDF, and check each value sits inside
   its box. Click **Approve selected document** as the manager.
4. **Run the practice drill.** Close Documents. Click **Practice
   drill**. Walk the SAMPLE deal through and certify it. Answer Yes to
   print the practice pack, then click Print all. Select each secure-stock
   item and click **Pull secure stock (selected)**.
   You should get the built-in pages plus the two-page title application.
   Every page shows SAMPLE at the top. The home screen then says "Last
   practice drill ... the SAMPLE pack printed in full. The sample tax
   check matched."
5. **Check alignment.** On the printed title application, each value
   should sit inside its box, clear of the printed labels. If the whole
   page is shifted, use **Alignment calibration** in the print window to
   nudge it, then reprint.
6. **Try click-to-place.** In Documents, select the demo form and click
   **Place fields on the form...**. Drag one value, such as the VIN, a
   little to the right. Save, click **Approve selected document** again, and print the form once more. The value
   should move by the same amount on paper.

## Photograph and send

- The installer's DONE window.
- The Step 0.8 math check showing three MATCH lines.
- Both printed pages of the title application, flat, whole page in frame.
- The home screen showing the last practice drill.
- Anything that went wrong: the message on screen, or
  `C:\DealerRevenueRescue\store\logs\launch-error.txt`.

## If a step fails

Stop at that step. Photograph the screen and the printed page. Nothing on
the PC needs to be undone: the app keeps its data in `C:\DealerRevenueRescue`,
and nothing leaves the PC.
