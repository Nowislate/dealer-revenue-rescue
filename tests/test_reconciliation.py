import importlib
import importlib.util
import json
import multiprocessing
import threading
from unittest.mock import patch
from pathlib import Path
from test_print_pipeline import PrintPipelineTestCase


def _mark_saved_deal(base, deal_id, snapshot_ready, release_snapshot, started, done, result):
    """Spawn-safe worker using persisted synthetic deal and completed pack."""
    from dealerpaperwork.audit import AuditLog
    from dealerpaperwork.deal_flow import DealSession, preset_from_setup_file
    from dealerpaperwork.print_config import PrintConfig
    from dealerpaperwork.print_pipeline import PrintSession
    from dealerpaperwork.printers import FakePrinterProvider
    from dealerpaperwork.reconciliation import ReconciliationStore
    from dealerpaperwork.users import UserStore

    try:
        base = Path(base)
        audit = AuditLog(base / 'audit.jsonl')
        actor = UserStore(base / 'users.json', audit_log=audit).authenticate('fay', 'fi-pin')
        deal = DealSession.load(deal_id, user=actor,
            preset=preset_from_setup_file(base / 'setup.json'),
            deals_dir=base / 'deals', audit_log=audit)
        session = PrintSession.load(deal_id, deal=deal, setup=base / 'setup.json',
            print_config=PrintConfig(path=base / 'print_config.json'),
            session_dir=base / 'print_sessions', provider=FakePrinterProvider(),
            audit_log=audit, actor=actor)

        class PausedLedger(ReconciliationStore):
            def _records(self):
                records = super()._records()
                if snapshot_ready is not None:
                    snapshot_ready.set()
                    if not release_snapshot.wait(10):
                        raise RuntimeError('First reconciliation snapshot was never released.')
                return records

        ledger = PausedLedger(base / 'reconciliation.json', audit_log=audit)
        started.set()
        ledger.mark(deal, actor=actor, print_session=session)
        result.put(None)
    except Exception as exc:
        result.put(repr(exc))
    finally:
        done.set()


class ReconciliationTests(PrintPipelineTestCase):
    def ledger(self):
        self.assertIsNotNone(importlib.util.find_spec('dealerpaperwork.reconciliation'), 'reconciliation ledger is missing')
        return importlib.import_module('dealerpaperwork.reconciliation').ReconciliationStore(
            Path(self.tmp.name) / 'reconciliation.json', audit_log=self.audit)

    def completed(self):
        deal = self.make_deal()
        ps = self.make_print_session(deal)
        self.finish_pack(ps)
        for row in ps.packaging_rows:
            ps.check_packaging_item(row.item_id, actor=self.fi, reference='SAMPLE paper original 001')
        return deal, ps

    def test_deal_stays_listed_until_named_reconciliation_persists(self):
        ledger = self.ledger()
        deal, ps = self.completed()
        self.assertEqual([d['deal_id'] for d in ledger.unreconciled(self.deals_dir, self.sessions_dir)], [deal.deal_id])
        ledger.mark(deal, actor=self.fi, print_session=ps)
        self.assertEqual(ledger.unreconciled(self.deals_dir, self.sessions_dir), [])
        reloaded = type(ledger)(ledger.path, audit_log=self.audit)
        self.assertEqual(reloaded.unreconciled(self.deals_dir, self.sessions_dir), [])
        events = [e for e in self.audit.read_events() if e.event_type == 'deal_reconciled']
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].actor, 'fay')
        self.assertTrue(events[0].timestamp)

    def test_concurrent_store_instances_preserve_both_reconciliations(self):
        first_deal, first_pack = self.completed()
        second_deal, second_pack = self.completed()
        first = self.ledger()
        second = self.ledger()
        snapshot_ready = threading.Event()
        release_snapshot = threading.Event()
        second_started = threading.Event()
        second_done = threading.Event()
        errors = []
        original_records = first._records

        def paused_records():
            records = original_records()
            snapshot_ready.set()
            if not release_snapshot.wait(10):
                raise RuntimeError('First reconciliation snapshot was never released.')
            return records

        def mark(ledger, deal, pack, *, is_second=False):
            if is_second:
                second_started.set()
            try:
                ledger.mark(deal, actor=self.fi, print_session=pack)
            except Exception as exc:
                errors.append(repr(exc))
            finally:
                if is_second:
                    second_done.set()

        with patch.object(first, '_records', side_effect=paused_records):
            threads = [threading.Thread(target=mark, args=(first, first_deal, first_pack)),
                threading.Thread(target=mark, args=(second, second_deal, second_pack),
                    kwargs={'is_second': True})]
            threads[0].start()
            try:
                self.assertTrue(snapshot_ready.wait(5))
                threads[1].start()
                self.assertTrue(second_started.wait(5))
                # Without a transaction the second writer commits the same old
                # snapshot before the paused writer replaces it.
                second_done.wait(.3)
            finally:
                release_snapshot.set()
                for thread in threads:
                    if thread.ident is not None:
                        thread.join(10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(set(json.loads(first.path.read_text())),
            {first_deal.deal_id, second_deal.deal_id})
        self.assertEqual(first.unreconciled(self.deals_dir, self.sessions_dir), [])
        # Repeating either named act remains idempotent across instances.
        second.mark(first_deal, actor=self.fi, print_session=first_pack)
        first.mark(second_deal, actor=self.fi, print_session=second_pack)
        events = [e for e in self.audit.read_events() if e.event_type == 'deal_reconciled']
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e.actor == 'fay' for e in events))

    def test_concurrent_processes_preserve_both_reconciliations(self):
        first_deal, _ = self.completed()
        second_deal, _ = self.completed()
        context = multiprocessing.get_context('spawn')
        snapshot_ready = context.Event()
        release_snapshot = context.Event()
        first_started, second_started = context.Event(), context.Event()
        first_done, second_done = context.Event(), context.Event()
        results = context.Queue()
        workers = [context.Process(target=_mark_saved_deal,
            args=(self.tmp.name, first_deal.deal_id, snapshot_ready, release_snapshot,
                  first_started, first_done, results)),
            context.Process(target=_mark_saved_deal,
            args=(self.tmp.name, second_deal.deal_id, None, None,
                  second_started, second_done, results))]
        workers[0].start()
        try:
            self.assertTrue(snapshot_ready.wait(10))
            workers[1].start()
            self.assertTrue(second_started.wait(10))
            second_done.wait(.3)
        finally:
            release_snapshot.set()
            for worker in workers:
                if worker.pid is not None:
                    worker.join(10)
                    if worker.is_alive():
                        worker.terminate()
                        worker.join(5)
        self.assertEqual([worker.exitcode for worker in workers], [0, 0])
        self.assertEqual([results.get(timeout=2) for _ in workers], [None, None])
        ledger = self.ledger()
        self.assertEqual(set(json.loads(ledger.path.read_text())),
            {first_deal.deal_id, second_deal.deal_id})
        self.assertEqual(ledger.unreconciled(self.deals_dir, self.sessions_dir), [])
        events = [e for e in self.audit.read_events() if e.event_type == 'deal_reconciled']
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e.actor == 'fay' for e in events))

    def test_ledger_replace_failure_is_reported_and_does_not_reconcile_deal(self):
        ledger = self.ledger()
        deal, ps = self.completed()
        replace = Path.replace

        def fail_ledger_replace(path, target):
            if Path(target) == ledger.path:
                raise OSError('Synthetic ledger replacement failure')
            return replace(path, target)

        with patch.object(Path, 'replace', fail_ledger_replace):
            with self.assertRaisesRegex(OSError, 'Synthetic ledger replacement failure'):
                ledger.mark(deal, actor=self.fi, print_session=ps)
        self.assertEqual([r['deal_id'] for r in ledger.unreconciled(self.deals_dir, self.sessions_dir)],
            [deal.deal_id])
        ledger.mark(deal, actor=self.fi, print_session=ps)
        self.assertEqual(ledger.unreconciled(self.deals_dir, self.sessions_dir), [])

    def test_void_or_edit_returns_previously_reconciled_deal_to_list(self):
        ledger = self.ledger()
        deal, ps = self.completed()
        ledger.mark(deal, actor=self.fi, print_session=ps)
        ps.void_item('buyers_order', actor=self.fi, reason='new copy')
        self.assertEqual(len(ledger.unreconciled(self.deals_dir, self.sessions_dir)), 1)
        deal.set_money(selling_price='36000')
        self.assertEqual(len(ledger.unreconciled(self.deals_dir, self.sessions_dir)), 1)

    def test_incomplete_anonymous_or_drill_cannot_be_reconciled(self):
        ledger = self.ledger()
        deal = self.make_deal()
        ps = self.make_print_session(deal)
        with self.assertRaises(ValueError):
            ledger.mark(deal, actor=self.fi, print_session=ps)
        self.finish_pack(ps)
        for row in ps.packaging_rows:
            ps.check_packaging_item(row.item_id, actor=self.fi, reference='SAMPLE paper original 001')
        with self.assertRaises(ValueError):
            ledger.mark(deal, actor='', print_session=ps)
        deal.is_drill = True
        deal.save()
        self.assertEqual(ledger.unreconciled(self.deals_dir, self.sessions_dir), [])

    def test_truncated_deal_and_session_are_visible_without_blocking_list(self):
        ledger = self.ledger()
        deal, ps = self.completed()
        (self.deals_dir / 'truncated.json').write_text('{')
        ps.file_path.write_text('{')
        rows = ledger.unreconciled(self.deals_dir, self.sessions_dir)
        self.assertEqual({r['deal_id'] for r in rows}, {deal.deal_id, 'truncated'})
        self.assertTrue(all(r.get('record_error') for r in rows))

    def test_corrupt_reconciliation_ledger_remains_unresolved_and_cannot_be_overwritten(self):
        ledger = self.ledger()
        deal, ps = self.completed()
        ledger.path.write_text('{')
        rows = ledger.unreconciled(self.deals_dir, self.sessions_dir)
        self.assertEqual([r['deal_id'] for r in rows], [deal.deal_id])
        self.assertTrue(ledger.read_error)
        with self.assertRaises(ValueError):
            ledger.mark(deal, actor=self.fi, print_session=ps)
        self.assertEqual(ledger.path.read_text(), '{')
