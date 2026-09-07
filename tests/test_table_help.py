import ast
import unittest
from pathlib import Path

from perpscanner.table_help import (
    BEST_SETUPS_COLUMN_HELP,
    HTF_EXPANSION_COLUMN_HELP,
    LTF_IGNITION_COLUMN_HELP,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class LTFIgnitionColumnHelpTests(unittest.TestCase):
    def test_every_ltf_ignition_column_has_human_readable_help(self):
        expected_columns = {
            "symbol",
            "ignition_state",
            "ignition_tf",
            "ltf_ignition_score",
            "conviction_net",
            "atr_percentile",
            "atr_roc",
            "atr_compression_score",
            "atr_expansion_score",
            "volume_zscore",
            "oi_zscore",
            "taker_imbalance",
            "cvd_3bar_slope",
            "basis_bp",
            "basis_delta_3bar_bp",
            "price_distance_from_vwap_atr",
            "breakout_distance_atr",
            "break_hold_confirmed",
            "rs_vs_btc",
            "rs_vs_eth",
            "compression_recent_bars",
            "bars_since_trigger",
            "tf_alignment_score",
            "ignition_score_5m",
            "ignition_score_15m",
            "ignition_score_1h",
        }

        self.assertEqual(set(LTF_IGNITION_COLUMN_HELP), expected_columns)
        self.assertTrue(all(text.strip() for text in LTF_IGNITION_COLUMN_HELP.values()))

    def test_ltf_renderer_wires_help_into_every_column_config(self):
        tree = ast.parse((REPO_ROOT / "perpscanner" / "ui_pages.py").read_text(encoding="utf-8"))
        renderer = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_show_ltf_ignition_table"
        )
        configured_columns = {}
        for node in ast.walk(renderer):
            if not isinstance(node, ast.Dict):
                continue
            string_keys = [key.value for key in node.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)]
            if set(string_keys) != set(LTF_IGNITION_COLUMN_HELP):
                continue
            for key, value in zip(node.keys, node.values):
                help_keywords = [keyword for keyword in value.keywords if keyword.arg == "help"]
                configured_columns[key.value] = len(help_keywords)

        self.assertEqual(set(configured_columns), set(LTF_IGNITION_COLUMN_HELP))
        self.assertTrue(all(count == 1 for count in configured_columns.values()))


class HTFExpansionColumnHelpTests(unittest.TestCase):
    def test_every_htf_expansion_column_has_human_readable_help(self):
        expected_columns = {
            "symbol",
            "htf_expansion_direction",
            "htf_expansion_score",
            "htf_atr_percentile",
            "htf_atr_roc",
            "htf_atr_compression_score",
            "htf_atr_expansion_score",
            "htf_breakout_distance_atr",
            "htf_range_width_atr",
            "daily_structure_score",
            "daily_atr_percentile",
            "daily_volume_ratio",
            "daily_volume_persistence_days",
            "daily_oi_persistence_days",
            "daily_swing_high",
            "daily_swing_low",
            "daily_long_confirmed",
            "daily_short_confirmed",
            "btc_daily_regime",
            "btc_daily_regime_score",
            "htf_momentum_score",
            "htf_setup_score",
            "htf_relative_strength_score",
            "volume_score",
            "oi_score",
            "rs_24h",
            "rs_72h",
        }

        self.assertEqual(set(HTF_EXPANSION_COLUMN_HELP), expected_columns)
        self.assertTrue(all(text.strip() for text in HTF_EXPANSION_COLUMN_HELP.values()))

    def test_htf_renderer_wires_help_into_every_column_config(self):
        tree = ast.parse((REPO_ROOT / "perpscanner" / "ui_pages.py").read_text(encoding="utf-8"))
        renderer = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_show_htf_expansion_table"
        )
        configured_columns = {}
        for node in ast.walk(renderer):
            if not isinstance(node, ast.Dict):
                continue
            string_keys = [key.value for key in node.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)]
            if set(string_keys) != set(HTF_EXPANSION_COLUMN_HELP):
                continue
            for key, value in zip(node.keys, node.values):
                help_keywords = [keyword for keyword in value.keywords if keyword.arg == "help"]
                configured_columns[key.value] = len(help_keywords)

        self.assertEqual(set(configured_columns), set(HTF_EXPANSION_COLUMN_HELP))
        self.assertTrue(all(count == 1 for count in configured_columns.values()))


class BestSetupsColumnHelpTests(unittest.TestCase):
    def test_every_best_setups_column_has_human_readable_help(self):
        expected_columns = {
            "symbol",
            "best_setup_state",
            "best_setup_score",
            "ignition_state",
            "ignition_tf",
            "ltf_ignition_score",
            "htf_expansion_direction",
            "htf_expansion_score",
            "daily_structure_score",
            "daily_volume_persistence_days",
            "daily_oi_persistence_days",
            "btc_daily_regime",
            "alignment_score",
            "tf_alignment_score",
            "bars_since_trigger",
            "volume_zscore",
            "oi_zscore",
            "taker_imbalance",
            "cvd_3bar_slope",
            "basis_bp",
            "basis_delta_3bar_bp",
            "atr_percentile",
            "atr_roc",
            "breakout_distance_atr",
            "rs_vs_btc",
            "rs_vs_eth",
        }

        self.assertEqual(set(BEST_SETUPS_COLUMN_HELP), expected_columns)
        self.assertTrue(all(text.strip() for text in BEST_SETUPS_COLUMN_HELP.values()))

    def test_best_setups_renderer_wires_help_into_every_column_config(self):
        tree = ast.parse((REPO_ROOT / "perpscanner" / "ui_pages.py").read_text(encoding="utf-8"))
        renderer = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_show_best_setups_table"
        )
        configured_columns = {}
        for node in ast.walk(renderer):
            if not isinstance(node, ast.Dict):
                continue
            string_keys = [key.value for key in node.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)]
            if set(string_keys) != set(BEST_SETUPS_COLUMN_HELP):
                continue
            for key, value in zip(node.keys, node.values):
                help_keywords = [keyword for keyword in value.keywords if keyword.arg == "help"]
                configured_columns[key.value] = len(help_keywords)

        self.assertEqual(set(configured_columns), set(BEST_SETUPS_COLUMN_HELP))
        self.assertTrue(all(count == 1 for count in configured_columns.values()))


if __name__ == "__main__":
    unittest.main()
