# Locked design decisions

These decisions come from the build specification ("Dealer Paperwork MVP —
Build Specification"). They are settled. If one needs to change, change the
specification first, then the code — the code follows the spec, never the
other way round.

1. **Dealer-neutral. No dealer is pinned to this build.** Dealer identity
   (name, address, contact, licence, franchise), title-filing method, and
   all store fee amounts are entered by the user during setup (Step 0.2)
   and approved by a manager. A fresh install contains no dealer identity
   or fee values anywhere. Sample values in `config/sample_preset.json`
   and in the tests are test data only.
2. **Preset → edit → approve.** Everything the app needs to run a deal is a
   preset the dealer can see and edit. An approved preset set is the
   dealer's certified configuration; deals run only on the last approved
   set. Approval is a named act (approver + timestamp) and is recorded.
3. **The user certifies before anything prints clean.** Per deal, a named
   user is walked through entry, reviews the money worksheet, and
   certifies the calculations under their name and role. An uncertified
   deal can only be saved or printed as a watermarked DRAFT. Editing a
   certified deal's money, buyer, vehicle, or trade clears the
   certification.
4. **Windows only.** No Mac support. One Windows machine in the store
   hosts the app; workstations reach it over the store's local network;
   printing runs through the Windows print spooler on the host (tray
   selection and long-stock handling depend on it).
5. **Local network only. No internet dependency, no external
   connections.** No lender platforms, credit bureaus, EFS/ETR, DMS, CRM,
   or cloud services; no phone-home. The app must install, run, save, back
   up, and print with the internet disconnected. Customer data never
   leaves the building through this app.
6. **Repo handoff.** Delivery is this repository, handed to the dealer's
   IT team, who deploy it. Updates (tax tables, presets, form versions)
   arrive as deliberate release packages the IT team installs and the
   dealer approves — never silent pushes.
7. **The dealer handles legal and technical gates.** Legal review,
   security review, deployment, user accounts, and go-live approval are
   the dealer's and its IT team's responsibility. The app fits the
   dealer's FTC SOPs; it does not replace them.
8. **Deterministic calculation engine; no AI in the numbers.** The
   Florida retail engine implements the specification's 12-step order
   exactly, with the Section 9 sample fixture as an acceptance test
   ($26,199 taxable base → $1,571.94 state tax + $75.00 surtax =
   $1,646.94 total tax). Money is `Decimal`, rounded to cents at the
   steps the spec fixes.
9. **The dealer adds its own documents.** Upload, name, tick when it
   prints, click the spots, pick the fields, save into the pack. Fields
   come only from the deal's data list — a document can never invent its
   own math. The app populates dealer-supplied forms; it never composes
   contract language.
10. **Four items are flagged unverified in spec Section 16 — do not
    hard-code any of them:** surtax base when a trade drops the taxable
    base below $5,000; the plate-transfer fee ($4.10 vs $4.60 conflict);
    lease taxation of upfront amounts (gated the Build 2 lease path —
    RESOLVED by decision 13, Lane's 2026-11-02 ruling, which see);
    current-year revisions (DR-15DSS rates, HSMV forms, statutes must be
    re-pulled at build time). County surtax rates are preset data loaded
    from the current-year DR-15DSS, never hard-coded.
11. **Equipment is the user's responsibility.** The LAW-type long
    contract is extremely long; the setup wizard's equipment preflight
    requires a named user to attest, on the store's actual long stock,
    that the printer handles it. Setup does not complete past a printer
    that fails the preflight.
12. **All documents are letter size (2026-11-01, Stage 5).** Because
    of the migration to digital contracting, every document in the
    pack — the LAW retail installment contract included — is now
    standard 8.5x11 letter; there are no long contracts to print.
    This retires the long-stock machinery the earlier stages built
    around decision 11: the contract's separate long-stock document
    class, the setup tray assignment for long stock, the preflight's
    long-stock attestation (now: a letter test print the user attests
    prints the full page legibly — the named attestation itself stays
    mandatory), and the print pipeline's stock-loaded confirmation
    gate for the contract. The generic machinery stays — multi-tray
    printers, per-document tray/paper presets, and calibration are
    still real — and the confirmation callback remains available in
    the pipeline for a future special-stock document, but nothing in
    the standard pack is gated on it. Decision 11's principle stands:
    having the right printing equipment is the store's responsibility,
    proven by the preflight before an outage does.
13. **Lease tax is charged on the base monthly payment only (Lane,
    2026-11-02).** In a lease, nothing is taxed item-by-item. The
    Florida lease tax rule is one rule: tax = base monthly payment ×
    combined rate — the 6% state rate plus the buyer's
    residence-county surtax rate — applied to the FULL payment. Each
    lease payment is its own taxable transaction, so the retail
    engine's $5,000 surtax cap (spec Section 9, step 9) does NOT apply
    to leases and the lease engine must not inherit it. Fees,
    products, and every other capitalized amount are taxed only
    through the payment they sit inside, because they change the
    adjusted cap cost and therefore the payment; an amount collected
    at signing is worksheet arithmetic (first payment with its tax,
    plus entered upfront charges and any security deposit), never a
    second tax calculation. This ruling resolves the lease item in
    decision 10's unverified list: the Stage 6 lease path never needs
    an item-by-item ruling on the taxation of upfront amounts. It is
    confirmed in production data: two real DMS lease worksheets
    supplied 2026-11-02 show Monthly Tax at exactly the combined
    rate × the base payment, with the worksheets' own "Cap Taxes"
    line at 0.00 (the Stage 6 acceptance fixtures, now synthetic per
    decision 17). Related: the
    Florida rental surcharge (§212.0606, $2/day for the first 30
    days = $60) is handled as a dealer-entered capitalized fee line
    (a preset value on the lease preset, e.g. the worksheet's cap
    fees line) — the amount is preset data, never hard-coded.
14. **The electronic registration filing fee is taxed (Lane,
    2026-11-02).** The electronic filing fee is a dealer charge for a
    private tag service, not a government agency fee, so it belongs
    in the retail taxable base with the other step 3 taxable
    dealer-required charges — not added after tax at step 12. This
    supersedes the spec's original step-12 treatment of the fee
    (outside the tax lines, per the Section 9 fixture's "not used in
    the tax lines" wording) as implemented in the retail engine. The
    fee's face value is counted once, inside the gross price; the net
    effect on a deal's amount due is exactly the tax on the fee (6% ×
    $199 = $11.94 at the sample amount). Confirmed against production
    data: a real DMS used-vehicle retail worksheet (details kept with
    the project) reproduces exactly; a synthetic deal in the same
    layout stands as the engine's regression fixture (decision 17). The bare Section 9 acceptance fixture in
    decision 8 carries no tag fee in its inputs and is unchanged. The
    lease path is untouched: lease tax is charged on the payment
    (decision 13), so a capitalized fee there is taxed only through
    the payment it sits inside.
15. **Standalone island mode: direct-attached printer, no LAN
    required (Lane, 2026-11-02).** Printing goes through the local
    Windows spooler (printers.py), so a printer connected directly
    to the workstation (e.g. USB) is indistinguishable from a
    network printer to the app. A single workstation with a
    direct-attached printer is therefore a complete deployment: no
    LAN, no network trust, no shared printer. This is the
    ransomware-mode configuration raised by a pilot store's
    scenario (the store's network isolated or untrusted): the dealer
    designates the machine in its incident plan and keeps it
    clean. The LAN in the original design serves multi-workstation
    sharing only; it was never a dependency of the print path. No
    code change — this records a deployment fact the setup
    guidance should state plainly.
    Implementation note (2026-11-18): setup Step 0.1 still refused a
    PC with no store network address, which contradicted this
    decision. Step 0.1 now also offers "This computer runs by
    itself", recorded by name in the setup file. The plain-words
    guidance is `docs/SETUP-ONE-PC.md`.

16. **Full-page form images; JPEG-only intake for v1 (Lane,
    2026-11-02).** At setup, the dealer exports each blank form page
    once from the store's DocuSign template or Acrobat at 300 DPI.
    The document library stores one full-page JPEG image per form
    page, in page order; the dealer's source PDF does not remain
    a PDF inside the app for new documents. There is no PDF merge.
    The existing overlay writer composes each page itself: form
    image first, mapped values and required markings on top, one
    pass onto blank letter paper. JPEG-only intake is confirmed for
    v1. Placement coordinates, certification rules, the spooler
    print path and calculation engines retain their existing
    contracts. This supersedes the old proposed PDF-merge task.

17. **Open-source preparation: synthetic fixtures, Apache 2.0 (Lane,
    2026-11-03).** To keep the option of publishing the code, the
    repo holds no real deal, stock, store, or customer data. The real
    DMS worksheets that first proved the engines (two lease, one
    used retail) are replaced in the tests by synthetic deals in the
    same layout; every expected figure was recomputed by hand from
    the worksheet formulas, independently of the engine. The pilot
    store is not named. The license is Apache 2.0 (patent grant and
    no-warranty terms). Publication, if it happens, uses a fresh git
    history, because this repo's history still holds the original
    fixtures. Publishing itself and the product name remain open.

18. **Bootstrap disclaimer acceptance is the first audit entry (Lane,
    2026-11-12, recorded during the native safety build).** A fresh
    installation creates only its first admin identity before acceptance.
    Acceptance is recorded first; the bootstrap account-creation record
    follows with the original creation time. Preserve existing audit
    history on existing installations and IT recovery; never rewrite it
    to manufacture a new first entry. All work follows named acceptance.
19. **Separately supplied paper forms may be checked off (Lane,
    2026-11-12, recorded during the native safety build).** If a required
    packaging form is absent from the resolved app pack, show the missing
    item and permit a named acknowledgment of a separately supplied paper
    form. Record actor, time and the paper-form acknowledgment; printing
    a generated summary is never evidence that the actual form exists.
    Secure-stock forms remain manual originals, never substitutes printed
    by the app. No new forms are bundled by this ruling.
20. **Name and release: Dealership Revenue Rescue, open source plus a
    download (Lane, 2026-11-19).** The product is "Dealership Revenue
    Rescue", always paired with the line "Print your deal paperwork when
    the DMS is down." The short form "Revenue Rescue" names the window,
    the desktop shortcut, the install folder (`C:\RevenueRescue`) and the
    release zip. The Python package keeps its name, `dealerpaperwork`.
    Release 1.0.0 is published as a public repository with a fresh
    one-commit history (decision 17), plus a download on nowislate.com.
    The public tree is written by `scripts/public_tree.py`, which leaves
    out internal build notes and runs `scripts/scan_public_tree.py` with
    the private term list; CI runs the same export and scan on every
    change. The first print test runs on a Windows PC and printer the
    owner provides.
21. **Setup proves the math against the store's own DMS (Lane,
    2026-11-19).** Setup gains two steps before approval. Step 0.6: the
    store enters its own county surtax rates from the current DR-15DSS
    (typed as percents, one county per line), confirms the state rate,
    and says whether it writes leases; if it does, it enters the lessor
    and title holder. Step 0.7, "Check the math against your DMS": a
    manager types in one recent real finance deal from the store's DMS,
    and the DMS's own total sales tax, amount financed and monthly
    payment. The app runs that deal through the same code a deal runs,
    with the store's own fees and county rates, and shows each figure
    line by line, matching or different, to the cent. A store that
    leases does the same for one lease (base payment, monthly tax,
    monthly payment). Setup cannot finish until every figure matches,
    or a named manager records why not in a full sentence. That record
    is audit-logged (`setup_math_check_override`) and kept in the local
    setup file; it is tied to the exact figures, so a later fee, rate or
    entry change re-opens the check. The check deal carries no customer
    name, VIN or stock number, and never leaves the PC. Florida only
    at the time; decision 22 widens this to any US state. Found while building it: before this ruling no setup screen
    entered county rates or the lessor, so setup in the window could
    never finish and a lease could never start. Version stays 1.0.0,
    because nothing was released.
22. **Any US store sets up its own tax and tag rules; release waits for
    it (Lane, 2026-11-20).** Lane: "Hold the release for all states."
    Onboarding "should include their taxation and tag rules which would
    mean that while Florida is already done the process would make it
    available to any dealer in the United States." This replaces the
    Florida-only line in decision 21. Setup gains a step, "Your state's
    tax and tag rules": a rule sheet of plain questions (what tax the
    state charges on a vehicle sale, the state rate, whose address picks
    the local rate, local rates and any limit on them, a most-tax cap,
    trade-in credit, whether the rebate comes off before tax, which
    charges are taxed, title and plate fees, how a lease is taxed, and
    a required disclosure line). Florida is a built-in sheet and its
    answers do not change: every Florida figure must stay the same to
    the cent. No other state ships pre-filled, because that would claim
    a state's law no one has proven against a real worksheet; a state
    joins the built-in list only the way Florida did. A rule the sheet
    cannot say stops setup with a plain message, never a guess. The
    math check (decision 21) proves typed rules against the store's own
    DMS deals before the app runs. Lane, 2026-11-20 (card): a store
    with typed rules must match its DMS on two retail deals, one with a
    trade and one without, plus one lease if it leases, with no manager
    override. Florida keeps decision 21 as built. Version 1.0.0 is not released until this
    ships. The home paper test still stands.
23. **Name: Dealer Revenue Rescue, never shortened (Lane, 2026-11-19;
    recorded 2026-11-24).** Lane: "revenue rescue has some collisions in
    product names, so we have updated that to dealer revenue rescue."
    This amends decision 20. The product is "Dealer Revenue Rescue" in
    every window title, the desktop shortcut, the launcher, the install
    folder (`C:\DealerRevenueRescue`), the release zip
    (`DealerRevenueRescue-<version>.zip`) and the docs. The short form
    "Revenue Rescue" is retired. The job line means the DMS, CRM or
    internet is down; it reads "Print your deal paperwork when the DMS,
    CRM or internet is down." until the owner words it otherwise. The
    Python package keeps its name, `dealerpaperwork`. Lane, 2026-11-23:
    the public repository is renamed `dealer-revenue-rescue`. Version
    stays 1.0.0, because nothing was released.
24. **v1.0.0 ships without the owner's paper test (Lane, 2026-11-23).**
    The owner has no Windows PC. The Windows test on GitHub's Windows
    computers (install from the release zip, the full suite, the
    installed app driven screen by screen, a store's own contracts
    printed to PDF) stands in for the PC part of decisions 20 and 22.
    Paper on a laser printer is not proven before release. The README
    names the printer need (PDF direct print), and the store's own
    practice drill is its paper test before an outage. This replaces
    the "home paper test still stands" line in decision 22.
25. **Copyright holder and release 1.0.1 (Lane, 2026-11-24).** The
    copyright holder is Now is Late, Inc., named in `NOTICE`; `LICENSE`
    stays the verbatim Apache 2.0 text. Release 1.0.1 publishes the
    review fixes: the network check's wording says exactly what it does
    (one lookup of the PC's own name, no connection), the install guides
    give the SHA-256 check as the only proof of where the zip came from,
    and `install.json` records whether the installer's tests ran. The
    public repository takes 1.0.1 as a new commit on its one-commit
    history; no private history is published.
26. **Release 1.0.2 (Lane, 2026-11-24).** Release 1.0.2 publishes the
    installer fix from an outside review: the installer refuses any file
    its manifest does not list and copies only the listed program files.
    The README names what an IT approver should know (the installer runs
    the release's code, data files are plain text, backups are not
    encrypted). The public repository takes 1.0.2 as a new commit on its
    history; no private history is published.
27. **Release 1.0.3 (Lane, 2026-11-24).** Release 1.0.3 publishes the
    restore fix: a restore reads and checks the whole backup before it
    moves the store aside, so the installer's before-install backup in
    `store\backups` restores as the administrator guide says. It also
    carries four README corrections. The public repository takes 1.0.3
    as a new commit on its history; no private history is published.
