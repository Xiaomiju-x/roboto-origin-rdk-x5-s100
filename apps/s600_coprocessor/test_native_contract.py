import unittest

from vlm_once import parse_answer
from whisper_once import parse_transcription


class NativeContractTests(unittest.TestCase):
    def test_asr_metrics_are_not_a_question(self):
        text, metrics = parse_transcription(
            "[Transcription] 找到椅子\n[Performance] RTF: 0.1 TTFT: 80 ms"
        )
        self.assertEqual(text, "找到椅子")
        self.assertEqual(len(metrics), 1)

    def test_asr_requires_actual_callback(self):
        with self.assertRaises(RuntimeError):
            parse_transcription("model initialization failed")

    def test_vlm_answer_not_native_statistics(self):
        text, _ = parse_answer(
            "[Assistant] >>> 有桌子和椅子\n===== vit cost: 30 ms =====\n[User] <<< "
        )
        self.assertEqual(text, "有桌子和椅子")

    def test_vlm_requires_answer(self):
        with self.assertRaises(RuntimeError):
            parse_answer("SUCCESS engine loaded")


if __name__ == "__main__":
    unittest.main()
