import unittest
from datetime import datetime
import ft_pipeline_health as h
import ft_price_writer as p


class PersistenceTests(unittest.TestCase):
    def fixture(self):
        now=datetime(2026,10,14,15,0,tzinfo=p.TORONTO)
        t=p.serial(now)-.01
        intake=[[t,"Partial",2,2,1,0,0,"Finalized; broad backlog remains",t]]
        reviews=[[t,"Daily",t,"portfolio","risk","TRIM ALAB to5%; other holdings HOLD","sources","audit","partial coverage","Daily:2026-10-14"]]
        snaps=[[t,"Daily:2026-10-14","ALAB"]+[""]*7+["TRIM"]+[""]*7+["Verified completed session"]]
        return now,intake,reviews,snaps,{"ALAB"},t

    def test_partial_coverage_can_have_verified_persistence(self):
        research,review,faults=h.evaluate(*self.fixture())
        self.assertEqual((research,review,faults),("SAVED PARTIAL","SAVED",[]))

    def test_started_is_not_complete(self):
        f=list(self.fixture());f[1][0][7]="Started; analysis/write verification pending"
        self.assertEqual(h.evaluate(*f)[0],"STALLED")
        self.assertTrue(h.evaluate(*f)[2])

    def test_saved_batch_without_completion_stamp_is_not_final(self):
        f=list(self.fixture());f[1][0][8]=""
        self.assertEqual(h.evaluate(*f)[0], "UNFINALIZED")
        self.assertTrue(h.evaluate(*f)[2])

    def test_missing_and_duplicate_snapshots_fail(self):
        for snapshots in ([],self.fixture()[3]*2):
            f=list(self.fixture());f[3]=snapshots
            self.assertEqual(h.evaluate(*f)[1],"INCOMPLETE")

    def test_a_morning_review_does_not_certify_afternoon_refresh(self):
        f=list(self.fixture());f[2][0][0]=int(f[5])+.5
        self.assertEqual(h.evaluate(*f)[1],"PREWINDOW REFRESH MISSING")

    def test_new_research_requires_review_refresh(self):
        f=list(self.fixture());f[5]+=.005
        self.assertEqual(h.evaluate(*f)[1],"NEW INPUTS")

    def test_post_cutoff_evidence_is_deferred_to_next_day_without_hiding_saved_review(self):
        f=list(self.fixture())
        f[0]=datetime(2026,10,14,18,0,tzinfo=p.TORONTO)
        f[5]=p.serial(datetime(2026,10,14,17,46,tzinfo=p.TORONTO))
        research,review,faults=h.evaluate(*f)
        self.assertEqual(research,"SAVED PARTIAL")
        self.assertEqual(review,"NEXT-DAY INPUTS")
        self.assertEqual(faults,[])

    def test_post_cutoff_evidence_does_not_excuse_a_morning_only_review(self):
        f=list(self.fixture())
        f[0]=datetime(2026,10,14,18,0,tzinfo=p.TORONTO)
        f[2][0][0]=p.serial(datetime(2026,10,14,13,0,tzinfo=p.TORONTO))
        f[5]=p.serial(datetime(2026,10,14,17,46,tzinfo=p.TORONTO))
        research,review,faults=h.evaluate(*f)
        self.assertEqual(review,"PREWINDOW REFRESH MISSING")
        self.assertTrue(faults)

    def test_identical_failure_runs_write_one_record_per_checkpoint(self):
        stamp=p.serial(datetime(2026,10,14,14,50,tzinfo=p.TORONTO))
        original=[stamp,"FAILED","SAVED PARTIAL","INCOMPLETE",78,16,
                  "Reviews!A16:J16: missing snapshots","https://github.com/run/1","old-sha"]
        repeated=[stamp+.001,"FAILED","SAVED PARTIAL","INCOMPLETE",78,16,
                  "Reviews!A16:J16: missing snapshots","https://github.com/run/2","new-sha"]
        self.assertFalse(h.should_append_audit(original,repeated))
        self.assertEqual(h.audit_phase(stamp),"DECISION_DUE")

    def test_status_change_or_new_exact_fault_creates_audit(self):
        stamp=p.serial(datetime(2026,10,14,14,50,tzinfo=p.TORONTO))
        original=[stamp,"FAILED","SAVED PARTIAL","INCOMPLETE",78,16,
                  "Reviews!A16:J16: missing snapshots","run1","sha1"]
        for index, change in [(1,"SAVED"),(3,"SAVED"),
                              (6,"Sources!I53: missing receipt")]:
            changed=list(original)
            changed[index]=change
            self.assertTrue(h.should_append_audit(original,changed))
        later=list(original)
        later[0]=p.serial(datetime(2026,10,14,18,5,tzinfo=p.TORONTO))
        self.assertTrue(h.should_append_audit(original,later))

    def test_new_trading_day_or_deadline_checkpoint_records_audit(self):
        t=p.serial(datetime(2026,10,14,13,50,tzinfo=p.TORONTO))
        row=[t,"SAVED","SAVED PARTIAL","PENDING",78,16,"research done","run1","sha1"]
        after=list(row)
        after[0]=p.serial(datetime(2026,10,14,14,50,tzinfo=p.TORONTO))
        self.assertTrue(h.should_append_audit(row,after))
        after[0]=p.serial(datetime(2026,10,15,13,50,tzinfo=p.TORONTO))
        self.assertTrue(h.should_append_audit(row,after))

    def test_system_note_is_red_for_live_or_historical_failure(self):
        self.assertEqual(h.system_state_text(["Reviews!A16"],"SAVED PARTIAL","SAVED","DUE"),
                         "SYSTEM — ⚠ BACKEND: SEE CELL NOTE")
        self.assertIn("PREWINDOW DELIVERY MISSED",
                      h.system_state_text([],"SAVED PARTIAL","SAVED","MISSED"))
        self.assertIn("VERIFIED",h.system_state_text([],"SAVED COMPLETE","SAVED","ON TIME"))

    def test_snapshot_requires_real_date_action_and_coverage(self):
        for column, bad in ((0, 1), (0, "bad"), (10, ""), (18, "")):
            f=list(self.fixture());f[3][0][column]=bad
            self.assertEqual(h.evaluate(*f)[1], "INCOMPLETE")
