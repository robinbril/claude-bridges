from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'delegate'))
from task_state import usage_receipt, total_usage


class UsageTests(unittest.TestCase):
    def test_nested_agent_tokens_are_included_without_double_counting_parent_usage(self):
        model = {'inputTokens': 100, 'outputTokens': 30, 'cacheReadInputTokens': 50, 'cacheCreationInputTokens': 0}
        receipt = usage_receipt({'usage': {'input_tokens': 100}, 'modelUsage': {'parent': model, 'child': model}})
        total = total_usage([{'usage': receipt}])
        self.assertEqual(total['total_tokens'], 360)
        self.assertEqual(receipt['scope'], 'model_tree')
        self.assertEqual(len(receipt['models']), 2)

    def test_top_level_usage_never_claims_to_be_a_whole_tree_total(self):
        receipt = usage_receipt({'usage': {'input_tokens': 1, 'output_tokens': 2,
            'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 0}})
        self.assertEqual(receipt['input_tokens'], 1)
        self.assertIsNone(total_usage([{'usage': receipt}])['total_tokens'])

    def test_zeroed_crash_usage_is_unknown(self):
        receipt = usage_receipt({'subtype': 'error_during_execution', 'modelUsage': {'parent': {
            'inputTokens': 0, 'outputTokens': 0, 'cacheReadInputTokens': 0, 'cacheCreationInputTokens': 0}}})
        self.assertFalse(receipt['complete'])

    def test_invalid_model_usage_does_not_become_zero(self):
        for invalid in (-1, True, '12', None):
            receipt = usage_receipt({'modelUsage': {'parent': {'inputTokens': invalid}}})
            self.assertFalse(receipt['complete'])
            self.assertIsNone(receipt['input_tokens'])


if __name__ == '__main__':
    unittest.main()
