import copy
from datetime import date
import unittest
import ft_rotation_replay as replay
import ft_sector_rotation as s
from ft_rotation_universe import SECTORS, INDUSTRIES


class ReplayTests(unittest.TestCase):
    def test_future_information_cannot_change_any_replayed_signal(self):
        end=date(2026,10,15);days=s.session_window(end,30)
        hist={t:{d:100+i for i,d in enumerate(days)} for t in ["SPY",*SECTORS,*INDUSTRIES]}
        baseline=replay.replay(hist,date(2026,10,1),end)
        altered=copy.deepcopy(hist)
        for series in altered.values():series[date(2026,10,16)]=99999
        self.assertEqual(baseline,replay.replay(altered,date(2026,10,1),end))

    def test_missing_semiconductor_history_is_explicit_not_invented(self):
        end=date(2026,10,15);days=s.session_window(end,30)
        hist={t:{d:100+i for i,d in enumerate(days)} for t in ["SPY",*SECTORS]}
        result=replay.replay(hist,end,end)
        self.assertIsNone(result["daily"][0]["healthcare_semiconductor_3d_pp"])
