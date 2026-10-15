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

    def test_snapshot_requires_real_date_action_and_coverage(self):
        for column, bad in ((0, 1), (0, "bad"), (10, ""), (18, "")):
            f=list(self.fixture());f[3][0][column]=bad
            self.assertEqual(h.evaluate(*f)[1], "INCOMPLETE")
