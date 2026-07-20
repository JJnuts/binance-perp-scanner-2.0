import unittest

import pandas as pd

from perpscanner.research import _newey_west_t_stat, _overlap_lag, _spearman_corr


class ResearchStatisticsTests(unittest.TestCase):
    def test_spearman_corr_handles_ties_without_scipy(self):
        left = pd.Series([1.0, 2.0, 2.0, 4.0])
        right = pd.Series([10.0, 20.0, 20.0, 40.0])
        self.assertAlmostEqual(_spearman_corr(left, right), 1.0)

    def test_overlap_lag_tracks_shared_forward_window(self):
        timestamps = pd.date_range("2026-01-01", periods=20, freq="5min")
        self.assertEqual(_overlap_lag(timestamps, 1.0), 11)

    def test_newey_west_reduces_repeated_observation_confidence(self):
        values = [0.20] * 8 + [-0.05] * 8 + [0.20] * 8 + [-0.05] * 8
        naive = _newey_west_t_stat(values, 0)
        adjusted = _newey_west_t_stat(values, 7)
        self.assertGreater(naive, adjusted)


if __name__ == "__main__":
    unittest.main()
