import unittest

from scene import intent, plan


class SceneTests(unittest.TestCase):
    def test_empty_is_unknown(self):
        r, _ = plan([])
        self.assertEqual(r["status"], "START_OCCUPIED_OR_UNKNOWN")

    def test_near_obstacle_not_removed(self):
        r, _ = plan([(0, 0.034, 100)])
        self.assertEqual(r["status"], "START_OCCUPIED_OR_UNKNOWN")

    def test_open_fixture(self):
        r, _ = plan([(a, 4.0, 100) for a in range(360)])
        self.assertEqual(r["status"], "SENSOR_FRAME_CANDIDATE")
        self.assertAlmostEqual(r["path_m"][-1][0], 2.0)

    def test_target_is_request_only(self):
        self.assertEqual(intent("找到前面的椅子")["class_id"], 56)
        self.assertEqual(intent("走过去")["intent"], "REJECTED")


if __name__ == "__main__":
    unittest.main()
