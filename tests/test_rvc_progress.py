import unittest

from app.yue2_app.rvc_training import stage_progress, training_progress


class RvcProgressTests(unittest.TestCase):
    def test_extracts_latest_helper_progress(self):
        self.assertEqual(stage_progress('进度：1/31\n进度: 30 / 31'),
                         {'completed': 30, 'total': 31, 'progress': 30 / 31})

    def test_rejects_invalid_helper_progress(self):
        self.assertEqual(stage_progress('进度：32/31'), {})
        self.assertEqual(stage_progress('没有进度'), {})

    def test_training_parser_keeps_epoch_and_losses(self):
        result = training_progress('训练轮次：7\nloss_disc=4.1, loss_gen=2.2')
        self.assertEqual(result['epoch'], 7)
        self.assertEqual(result['losses'], {'disc': 4.1, 'gen': 2.2})


if __name__ == '__main__':
    unittest.main()
