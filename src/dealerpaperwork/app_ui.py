"""Shared native entry: bootstrap/sign-in → notice → setup/home.

All public GUI launchers delegate here. Low-level engines remain usable by
headless tests; only authenticated, accepting users reach native work screens.
"""
from __future__ import annotations
import json
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
    TK_AVAILABLE = True
except ImportError:
    TK_AVAILABLE = False

from .startup import StartupFlow
from .disclaimer import TEXT, VERSION
from .users import ROLES, ACTION_MANAGE_USERS, ACTION_APPROVE_PRESET, PIN_WARNING
from .doc_library import CONDITION_LEASE, CONDITION_RETAIL_NEW, CONDITION_RETAIL_USED


if TK_AVAILABLE:
    class Application:
        def __init__(self, root, flow, *, initial_action='home', **options):
            self.root, self.flow, self.options = root, flow, options
            self.initial_action = initial_action
            self.setup_prompted = False
            self.deals_dir = Path(options.get('deals_dir') or flow.root / 'data/deals')
            self.library_dir = Path(options.get('library_dir') or flow.root / 'data/documents')
            self.sessions_dir = flow.root / 'data/print_sessions'
            self.drill_runs_path = flow.root / 'data/drill_runs.json'
            self.print_config_path = Path(options.get('print_config_path') or flow.root / 'config/print_config.json')
            self.body = ttk.Frame(root, padding=16)
            self.body.pack(fill='both', expand=True)
            root.title('Dealer Revenue Rescue')
            root.geometry('840x720')
            self.show_stage()

        def clear(self):
            for child in self.body.winfo_children():
                child.destroy()

        def field(self, parent, label, *, secret=False):
            ttk.Label(parent, text=label).pack(anchor='w', pady=(6, 0))
            value = tk.StringVar()
            ttk.Entry(parent, textvariable=value, show='*' if secret else '').pack(fill='x')
            return value

        def attempt(self, callback):
            try:
                return callback()
            except Exception as exc:
                messagebox.showerror('Cannot continue', str(exc), parent=self.root)
                return None

        def show_stage(self):
            self.clear()
            stage = self.flow.stage
            if stage in ('bootstrap', 'sign_in'):
                self.show_identity(stage == 'bootstrap')
            elif stage == 'disclaimer':
                self.show_notice()
            else:
                self.show_home()
                action, self.initial_action = self.initial_action, 'home'
                if stage == 'setup' and not self.setup_prompted:
                    self.setup_prompted = True
                    self.root.after_idle(lambda: self.attempt(self.open_setup))
                elif action != 'home':
                    self.root.after_idle(lambda: self.attempt(lambda: self.open_action(action)))

        def show_identity(self, bootstrap):
            ttk.Label(self.body, text='Create the first admin account' if bootstrap else 'Sign in',
                      font=('Segoe UI', 16, 'bold')).pack(anchor='w')
            if bootstrap:
                ttk.Label(self.body, text='Identity only. Read and accept the notice before setup or deal work.', wraplength=760).pack(anchor='w', pady=10)
                ttk.Label(self.body, text=PIN_WARNING, wraplength=760).pack(anchor='w', pady=(0, 10))
            username = self.field(self.body, 'Username')
            name = self.field(self.body, 'Display name') if bootstrap else None
            secret = self.field(self.body, 'PIN / password', secret=True)
            confirmation = self.field(self.body, 'Confirm PIN / password', secret=True) if bootstrap else None

            def submit():
                if bootstrap:
                    self.flow.bootstrap(username=username.get(), display_name=name.get(),
                        secret=secret.get(), confirmation=confirmation.get())
                else:
                    self.flow.sign_in(username.get(), secret.get())
                secret.set('')
                if confirmation:
                    confirmation.set('')
                self.show_stage()
            ttk.Button(self.body, text='Create admin' if bootstrap else 'Sign in',
                       command=lambda: self.attempt(submit)).pack(anchor='e', pady=12)

        def show_notice(self):
            user = self.flow.user
            ttk.Label(self.body, text=f'Use notice · version {VERSION}', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
            ttk.Label(self.body, text=f'Accepting as {user.display_name} ({user.username}, {user.role})').pack(anchor='w', pady=8)
            frame = ttk.Frame(self.body)
            frame.pack(fill='both', expand=True)
            view = tk.Text(frame, wrap='word', font=('Segoe UI', 11), padx=10, pady=10)
            scrollbar = ttk.Scrollbar(frame, command=view.yview)
            view.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side='right', fill='y')
            view.pack(side='left', fill='both', expand=True)
            view.insert('1.0', TEXT)
            view.configure(state='disabled')
            ttk.Button(self.body, text='I accept', command=lambda: self.attempt(self.accept_notice)).pack(side='right', pady=12)
            ttk.Button(self.body, text='Exit', command=self.root.destroy).pack(side='left', pady=12)

        def accept_notice(self):
            self.flow.accept()
            self.show_stage()

        def authorized(self):
            return self.flow.require_access()

        def show_home(self):
            user = self.authorized()
            ready = self.flow.stage == 'home'
            ttk.Label(self.body, text='Dealer Revenue Rescue', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
            ttk.Label(self.body, text='Print your deal paperwork when the DMS, CRM or internet is down.').pack(anchor='w')
            ttk.Label(self.body, text=f'{user.display_name} · {user.role}').pack(anchor='w', pady=6)
            controls = ttk.Frame(self.body)
            controls.pack(fill='x', pady=8)
            for text, action in [('Setup', 'setup'), ('Accounts / PIN', 'accounts'),
                                 ('New retail deal', 'retail'), ('New lease deal', 'lease'),
                                 ('Practice drill', 'drill'), ('Documents', 'documents')]:
                button = ttk.Button(controls, text=text, command=lambda a=action: self.attempt(lambda: self.open_action(a)))
                button.pack(side='left', padx=(0, 6))
                if not ready and action not in ('setup', 'accounts'):
                    button.configure(state='disabled')
            if not ready:
                ttk.Label(self.body, text='Complete Step 0 before deal entry, document work or packaging.\nCreate a second admin before finishing setup.', wraplength=760).pack(anchor='w', pady=8)
                ttk.Button(self.body, text='Sign out', command=self.sign_out).pack(anchor='e', pady=8)
                return
            from .reconciliation import ReconciliationStore
            from .deal_flow import list_deals
            from .drill import last_drill_summary, readiness_line
            ttk.Label(self.body, text=readiness_line(last_drill_summary(self.drill_runs_path)),
                      wraplength=760).pack(anchor='w', pady=(0, 4))
            self.ledger = ReconciliationStore(self.flow.root / 'data/reconciliation.json', audit_log=self.flow.audit)
            pending = self.ledger.unreconciled(self.deals_dir, self.sessions_dir)
            ttk.Label(self.body, text=f'Unreconciled outage deals: {len(pending)}', font=('Segoe UI', 12, 'bold')).pack(anchor='w', pady=8)
            ttk.Label(self.body, text='After recovery, enter the DMS exactly as signed, compare and file, then mark reconciled.', wraplength=760).pack(anchor='w')
            self.deal_list = ttk.Treeview(self.body, columns=('buyer', 'state', 'recovery'), show='tree headings', height=14)
            self.deal_list.heading('#0', text='Deal')
            for col, title in [('buyer', 'Buyer'), ('state', 'State'), ('recovery', 'DMS recovery')]:
                self.deal_list.heading(col, text=title)
                self.deal_list.column(col, width=180)
            pending_ids = {d['deal_id'] for d in pending}
            errors = [d['record_error'] for d in pending if d.get('record_error')]
            if self.ledger.read_error and self.ledger.read_error not in errors:
                errors.insert(0, self.ledger.read_error)
            if errors:
                ttk.Label(self.body, text='Dealer IT recovery needed:\n' + '\n'.join(errors),
                          wraplength=760, foreground='#b00020').pack(anchor='w', pady=6)
            for deal in list_deals(self.deals_dir):
                self.deal_list.insert('', 'end', iid=deal['deal_id'], text=f"{deal['deal_id']} ({deal.get('deal_kind', 'retail')})",
                    values=(deal.get('buyer_name', deal.get('buyer', '')), deal.get('state', ''),
                            'Unreconciled' if deal['deal_id'] in pending_ids else 'Reconciled'))
            self.deal_list.pack(fill='both', expand=True, pady=8)
            actions = ttk.Frame(self.body)
            actions.pack(fill='x')
            for text, callback in [('Open deal', lambda: self.open_deal(self.selected())),
                                   ('Print / package selected', lambda: self.open_print(self.selected())),
                                   ('Mark reconciled', self.reconcile_selected), ('Refresh', self.show_stage)]:
                ttk.Button(actions, text=text, command=lambda cb=callback: self.attempt(cb)).pack(side='left', padx=(0, 6))
            ttk.Button(self.body, text='Sign out', command=self.sign_out).pack(anchor='e', pady=8)

        def selected(self):
            selection = self.deal_list.selection()
            if not selection:
                raise ValueError('Select a saved deal first.')
            return selection[0]

        def sign_out(self):
            self.flow.user = None
            self.setup_prompted = False
            self.show_stage()

        def child(self, title):
            previous_grab = self.root.grab_current()
            win = tk.Toplevel(self.root)
            win.title(title)
            win.transient(self.root)
            win._previous_grab = previous_grab
            win.grab_set()
            return win

        def wait_child(self, win):
            previous = win._previous_grab
            self.root.wait_window(win)
            if previous is not None and previous.winfo_exists():
                previous.grab_set()

        def open_action(self, action):
            self.authorized()
            if action == 'setup':
                return self.open_setup()
            if action == 'accounts':
                return self.open_accounts()
            if self.flow.stage != 'home':
                raise PermissionError('Complete setup first.')
            if action == 'retail':
                return self.open_retail()
            if action == 'lease':
                return self.open_lease()
            if action == 'drill':
                return self.open_drill()
            if action == 'documents':
                from .doc_library import DocumentLibrary
                from .docs_ui import DocumentsWindow
                win = self.child('Document library')
                DocumentsWindow(win, DocumentLibrary(self.library_dir), current_user=self.authorized())
                self.wait_child(win)
            if action == 'print':
                return self.open_print(self.options.get('deal_id') or self.selected())

        def open_setup(self):
            actor = self.authorized()
            if not actor.has_permission(ACTION_APPROVE_PRESET):
                raise PermissionError('An admin or manager must run setup. Sign out and ask one to sign in.')
            from .setup_ui import SetupWizardWindow
            from .setup_wizard import SetupWizard
            wizard = SetupWizard(setup_path=self.flow.setup_path,
                printer_provider=self.options.get('printer_provider'),
                user_store=self.flow.users, actor=actor, audit_log=self.flow.audit)
            win = self.child('Setup')
            SetupWizardWindow(win, wizard, manage_accounts=self.open_accounts)
            self.wait_child(win)
            self.show_stage()

        def open_retail(self, deal_id=None):
            actor = self.authorized()
            if self.flow.stage != 'home':
                raise PermissionError('Complete setup first.')
            from .deal_ui import DealWindow
            from .deal_flow import DealSession, preset_from_setup_file
            session = None
            if deal_id:
                raw = json.loads((self.deals_dir / (deal_id + '.json')).read_text(encoding='utf-8'))
                if raw.get('deal_kind') == 'lease':
                    raise ValueError('This saved deal is a lease. Open it with Open deal.')
                session = DealSession.load(deal_id, user=actor, preset=preset_from_setup_file(self.flow.setup_path),
                                          deals_dir=self.deals_dir, audit_log=self.flow.audit)
            win = self.child('Retail deal')
            DealWindow(win, user_store=self.flow.users, audit_log=self.flow.audit,
                setup_path=self.flow.setup_path, deals_dir=self.deals_dir,
                current_user=actor, session=session,
                product_forms=self.retail_product_forms())
            self.wait_child(win)
            self.show_stage()

        def retail_product_forms(self):
            from .products_ui import product_forms_by_condition
            return product_forms_by_condition(
                self.library_dir, (CONDITION_RETAIL_NEW, CONDITION_RETAIL_USED))

        def product_forms(self, *conditions):
            from .products_ui import product_forms_for
            return product_forms_for(self.library_dir, conditions)

        def open_deal(self, deal_id):
            self.authorized()
            raw = json.loads((self.deals_dir / (deal_id + '.json')).read_text(encoding='utf-8'))
            if raw.get('deal_kind') == 'lease':
                return self.open_lease(deal_id)
            return self.open_retail(deal_id)

        def open_lease(self, deal_id=None):
            actor = self.authorized()
            if self.flow.stage != 'home':
                raise PermissionError('Complete setup first.')
            from .lease_ui import LeaseWindow
            from .lease_flow import LeaseDealSession
            from .deal_flow import preset_from_setup_file
            session = None
            if deal_id:
                session = LeaseDealSession.load(deal_id, user=actor, preset=preset_from_setup_file(self.flow.setup_path),
                                                deals_dir=self.deals_dir, audit_log=self.flow.audit)
            win = self.child('Lease deal')
            LeaseWindow(win, setup_path=self.flow.setup_path, deals_dir=self.deals_dir,
                        audit_log=self.flow.audit, current_user=actor, session=session,
                        product_forms=self.product_forms(CONDITION_LEASE))
            self.wait_child(win)
            self.show_stage()

        def open_drill(self):
            """Practice drill: a SAMPLE deal through the walk-through, then
            an optional SAMPLE pack print, recorded as a drill run."""
            actor = self.authorized()
            if self.flow.stage != 'home':
                raise PermissionError('Complete setup first.')
            from .deal_ui import DealWindow
            from .deal_flow import preset_from_setup_file
            from .drill import readiness_line, run_drill
            from .print_ui import PrintWindow

            def walk(session):
                win = self.child('Practice drill')
                DealWindow(win, user_store=self.flow.users, audit_log=self.flow.audit,
                    setup_path=self.flow.setup_path, deals_dir=self.deals_dir,
                    current_user=actor, session=session,
                    product_forms=self.retail_product_forms())
                win.title('Dealer Revenue Rescue - Practice drill (every page prints SAMPLE)')
                self.wait_child(win)

            def print_pack(session):
                if not messagebox.askyesno('Print the practice pack',
                        'Print the practice pack now? Every page prints with SAMPLE '
                        'across it. Use blank letter paper.', parent=self.root):
                    return None
                _, print_session, config = self.load_print(session.deal_id)
                win = self.child('Practice drill - print')
                PrintWindow(win, session=print_session, print_config=config)
                self.wait_child(win)
                return print_session

            record = run_drill(actor, preset=preset_from_setup_file(self.flow.setup_path),
                setup_path=self.flow.setup_path, walk=walk, print_pack=print_pack,
                deals_dir=self.deals_dir, audit_log=self.flow.audit,
                runs_path=self.drill_runs_path)
            messagebox.showinfo('Drill recorded', readiness_line(record), parent=self.root)
            self.show_stage()

        def load_print(self, deal_id):
            actor = self.authorized()
            if self.flow.stage != 'home':
                raise PermissionError('Complete setup first.')
            from .deal_flow import DealSession, preset_from_setup_file
            from .lease_flow import LeaseDealSession
            from .doc_library import DocumentLibrary
            from .print_pipeline import PrintSession
            from .print_config import PrintConfig
            raw = json.loads((self.deals_dir / (deal_id + '.json')).read_text(encoding='utf-8'))
            cls = LeaseDealSession if raw.get('deal_kind') == 'lease' else DealSession
            deal = cls.load(deal_id, user=actor, preset=preset_from_setup_file(self.flow.setup_path),
                            deals_dir=self.deals_dir, audit_log=self.flow.audit)
            config = PrintConfig.load(self.print_config_path)
            kwargs = dict(deal=deal, setup=self.flow.setup_path, print_config=config,
                session_dir=self.sessions_dir, audit_log=self.flow.audit, actor=actor,
                library=DocumentLibrary(self.library_dir), provider=self.options.get('printer_provider'))
            if (self.sessions_dir / (deal_id + '.json')).exists():
                session = PrintSession.load(deal_id, **kwargs)
            else:
                session = PrintSession.from_setup(**kwargs)
            return deal, session, config

        def open_print(self, deal_id):
            from .print_ui import PrintWindow
            deal, session, config = self.load_print(deal_id)
            win = self.child('Print and package')
            PrintWindow(win, session=session, print_config=config)
            self.wait_child(win)
            self.show_stage()

        def reconcile_selected(self):
            deal, session, _ = self.load_print(self.selected())
            if messagebox.askyesno('Confirm DMS reconciliation',
                'Have you entered this deal exactly as signed, compared every figure, resolved variances with a manager, and scanned/filed the package?', parent=self.root):
                self.ledger.mark(deal, actor=self.authorized(), print_session=session)
                self.show_stage()

        def open_accounts(self):
            actor = self.authorized()
            win = self.child('Accounts and PIN recovery')
            win.geometry('540x700')
            frame = ttk.Frame(win, padding=14)
            frame.pack(fill='both', expand=True)
            ttk.Label(frame, text='Create at least two admin accounts.\nOne admin means one forgotten PIN can require dealer IT recovery.', wraplength=500).pack(anchor='w')
            if actor.has_permission(ACTION_MANAGE_USERS):
                username = self.field(frame, 'Username to create / reset')
                name = self.field(frame, 'Display name for new account')
                role = tk.StringVar(value='sales')
                ttk.Combobox(frame, textvariable=role, values=ROLES, state='readonly').pack(fill='x', pady=6)
                secret = self.field(frame, 'New PIN / password', secret=True)
                confirm = self.field(frame, 'Confirm new PIN / password', secret=True)
                ttk.Label(frame, text='Existing accounts: ' + ', '.join(self.flow.users.usernames()), wraplength=500).pack(anchor='w', pady=4)

                def account_action(reset):
                    if reset:
                        self.flow.users.reset_secret(actor=actor, username=username.get(),
                            secret=secret.get(), confirmation=confirm.get())
                    else:
                        self.flow.users.create_confirmed_user(actor=actor, username=username.get(),
                            display_name=name.get(), role=role.get(), secret=secret.get(), confirmation=confirm.get())
                    secret.set('')
                    confirm.set('')
                    messagebox.showinfo('Account saved', 'The account is ready for its next sign-in.', parent=win)
                ttk.Button(frame, text='Create account', command=lambda: self.attempt(lambda: account_action(False))).pack(anchor='w', pady=4)
                ttk.Button(frame, text='Reset existing account PIN', command=lambda: self.attempt(lambda: account_action(True))).pack(anchor='w', pady=4)
            ttk.Separator(frame).pack(fill='x', pady=10)
            ttk.Label(frame, text=f'Change your own PIN: {actor.username}').pack(anchor='w')
            current = self.field(frame, 'Current PIN / password', secret=True)
            replacement = self.field(frame, 'New PIN / password', secret=True)
            repeated = self.field(frame, 'Confirm new PIN / password', secret=True)

            def change():
                self.flow.users.change_secret(actor=actor, current_secret=current.get(),
                    secret=replacement.get(), confirmation=repeated.get())
                for var in (current, replacement, repeated):
                    var.set('')
                messagebox.showinfo('PIN changed', 'Use the new PIN at your next sign-in.', parent=win)
            ttk.Button(frame, text='Change my PIN', command=lambda: self.attempt(change)).pack(anchor='w', pady=6)
            self.wait_child(win)


def run_app(*, data_root=Path('.'), initial_action='home', setup_path=None,
            users_path=None, audit_path=None, **options):
    if not TK_AVAILABLE:
        print('Tkinter is unavailable. On the Windows host, install/repair Python with Tcl/Tk. No app data was changed.')
        return 1
    root = tk.Tk()
    try:
        flow = StartupFlow(data_root, setup_path=setup_path, users_path=users_path, audit_path=audit_path)
        Application(root, flow, initial_action=initial_action, **options)
        root.mainloop()
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
    return 0


if __name__ == '__main__':
    raise SystemExit(run_app())
