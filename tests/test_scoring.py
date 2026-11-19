import unittest

from spamhop.scoring import combine_scores, technical_adjustment


class ScoringTests(unittest.TestCase):
    def test_rspamd_adjustment_is_signed_and_bounded(self):
        positive = technical_adjustment(100, scale=6, max_adjustment=0.30)
        negative = technical_adjustment(-100, scale=6, max_adjustment=0.30)

        self.assertGreater(positive, 0)
        self.assertLessEqual(positive, 0.30)
        self.assertLess(negative, 0)
        self.assertGreaterEqual(negative, -0.30)

    def test_combination_clamps_to_probability_range(self):
        self.assertEqual(combine_scores(0.95, 0.30), 1.0)
        self.assertEqual(combine_scores(0.05, -0.30), 0.0)


if __name__ == "__main__":
    unittest.main()
