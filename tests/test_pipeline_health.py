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
        snaps[0][11:16]=[100,"USD","SPY",100,"PRIMARY:issuer"]
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

    def test_numeric_sheet_count_does_not_duplicate_issue(self):
        stamp=p.serial(datetime(2026,10,14,14,50,tzinfo=p.TORONTO))
        before=[stamp,"FAILED","SAVED PARTIAL","INCOMPLETE",78.0,16.0,
                "Reviews!A16: persisted gap","run1","sha1"]
        after=[stamp+.0002,"FAILED","SAVED PARTIAL","INCOMPLETE",78,16,
               "Reviews!A16: persisted gap","run2","sha2"]
        self.assertFalse(h.should_append_audit(before,after))

    def test_before_deadlines_cannot_show_verified(self):
        self.assertIn("PENDING",
                      h.system_state_text([],"PENDING","PENDING","DUE"))
        self.assertIn("PENDING",
                      h.system_state_text([],"SAVED PARTIAL","PENDING","DUE"))

    def test_system_note_is_red_for_live_or_historical_failure(self):
        self.assertEqual(h.system_state_text(["Reviews!A16"],"SAVED PARTIAL","SAVED","DUE"),
                         "SYSTEM — ⚠ BACKEND: SEE CELL NOTE")
        self.assertIn("PREWINDOW DELIVERY MISSED",
                      h.system_state_text([],"SAVED PARTIAL","SAVED","MISSED"))
        self.assertIn("VERIFIED",h.system_state_text([],"SAVED COMPLETE","SAVED","ON TIME"))

    def test_late_recovery_preserves_exact_missed_deadlines(self):
        now=datetime(2026,10,15,14,50,tzinfo=p.TORONTO)
        day=int(p.serial(now))
        intake=[[day+14.25/24,"Partial",108,0,2,0,1,"Finalized",day+14.55/24]]
        prices=[[day+14.60/24,"close","Complete",78,"verified"]]
        reviews=[[day+14.60/24,"Daily",day+14.60/24,"portfolio","risk","HOLD",
                  "sources","audit","limits","Daily:2026-10-15"]]
        status=h.delivery_status(now,prices,intake,reviews)
        self.assertIn("MISSED 2026-10-15",status)
        self.assertIn("Sources!I46=14:33 >13:40",status)
        self.assertIn("DAILY CLOSES!L5=14:36 >14:15",status)
        self.assertNotIn("review",status)

    def test_first_complete_price_receipt_after_1220_is_on_time(self):
        now=datetime(2026,10,15,14,50,tzinfo=p.TORONTO)
        day=int(p.serial(now))
        intake=[[day+13.25/24,"Partial",1,0,0,0,0,"Finalized",day+13.5/24]]
        prices=[[day+12.5/24,"pretrade","Complete",78,"verified"],
                [day+14.6/24,"close","Complete",78,"late retry"]]
        reviews=[[day+14.6/24,"Daily",day+14.6/24,"portfolio","risk","HOLD",
                  "sources","audit","limits","Daily:2026-10-15"]]
        self.assertTrue(h.delivery_status(now,prices,intake,reviews).startswith("ON TIME"))

    def test_rotation_rejects_stale_post_execution_weights(self):
        # META user-confirmed sale 12% -> 10%, but engine saved 12% held.
        now = datetime(2026, 10, 19, 14, 30, tzinfo=p.TORONTO)
        day = p.serial(now)
        receipt = [["ROTATION STATUS", "COMPLETE"],
                   ["COMPLETED AT", day],
                   ["PRICE SESSION", p.serial(datetime(2026, 10, 16))]]
        old_proposal = ["META", .12, .10, "HOLD", .10, "old pending",
                        day, p.serial(datetime(2026, 10, 16)), "", "",
                        "VERIFIED PRIOR CLOSE", "PENDING", "XLC"]
        live = [["META", "Meta Platforms", .10, .10]]
        faults = h.rotation_health(now, receipt, [old_proposal], live, [])
        self.assertTrue(any("PRICE MONITOR!B2051:C2051 stale META weights"
                            in issue and "Portfolio!C8:D8" in issue for issue in faults))
        refreshed = list(old_proposal)
        refreshed[1] = .10
        refreshed[11] = "CLEAR"
        self.assertFalse(h.rotation_health(now, receipt, [refreshed], live, []))

    def test_snapshot_requires_real_date_action_and_coverage(self):
        for column, bad in ((0, 1), (0, "bad"), (10, ""), (18, "")):
            f=list(self.fixture());f[3][0][column]=bad
            self.assertEqual(h.evaluate(*f)[1], "INCOMPLETE")
    
    def test_missing_company_is_documented_gap_not_broken_mapping(self):
        now = datetime(2026,10,26,10,45,tzinfo=p.TORONTO)
        row = [""]*32
        row[0],row[1] = "PSX",p.serial(now)
        row[15],row[16],row[17],row[18] = .3,None,0,None
        row[29],row[30],row[31] = "Company score UNKNOWN; issuer review required",0,"hash"
        card = [""]*32
        card[0],card[23],card[26] = "PSX",.3,0
        held = [["PSX","Phillips 66",.05,.05]]
        receipt = [["DECISION INPUTS","VERIFIED"]]
        self.assertEqual(h.decision_input_health(now,receipt,[card],[row],held),[])
        row[18],card[27] = .105,.105
        self.assertTrue(any("incorrectly certified" in x for x in h.decision_input_health(now,receipt,[card],[row],held)))
        row[18],card[27],row[17] = None,"",None
        self.assertTrue(any("price/macro input missing" in x for x in h.decision_input_health(now,receipt,[card],[row],held)))

    def test_sunday_rotation_preparation_not_false_friday_failure(self):
        now = datetime(2026,10,25,21,42,tzinfo=p.TORONTO)
        receipt = [["ROTATION STATUS","COMPLETE"],["COMPLETED AT",p.serial(now)],
                   ["PRICE SESSION",p.serial(datetime(2026,10,23))]]
        held = [["PSX","Phillips 66",.05,.05]]
        proposal = ["PSX",.05,.05,"HOLD",.05,"verified Friday close",
                    p.serial(now),p.serial(datetime(2026,10,23)),"","","VERIFIED PRIOR CLOSE",
                    "CLEAR","XLE"]
        self.assertEqual(h.rotation_health(now,receipt,[proposal],held,[]),[])
        self.assertTrue(h.delivery_status(
            datetime(2026,10,23,23,59,tzinfo=p.TORONTO),[],[],[]).startswith("MISSED"))
