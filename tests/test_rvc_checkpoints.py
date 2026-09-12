import json
from pathlib import Path
import tempfile
import unittest
import torch

from app.yue2_app.rvc_checkpoints import checkpoint_path, has_checkpoint, save_pair


class CheckpointRecovery(unittest.TestCase):
    def test_committed_pair_falls_back_after_interrupted_or_corrupt_save(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            generator, discriminator = torch.nn.Linear(2, 2), torch.nn.Linear(2, 1)
            optim_g, optim_d = torch.optim.Adam(generator.parameters()), torch.optim.Adam(discriminator.parameters())
            save_pair(directory, generator, discriminator, optim_g, optim_d, .001, 1)
            save_pair(directory, generator, discriminator, optim_g, optim_d, .001, 2)
            self.assertTrue(has_checkpoint(directory))
            self.assertEqual(Path(checkpoint_path(directory, 'G')).name, 'G_2.pth')
            (directory / 'D_2.pth').write_bytes(b'interrupted')
            self.assertEqual(Path(checkpoint_path(directory, 'G')).name, 'G_1.pth')
            self.assertEqual(Path(checkpoint_path(directory, 'D')).name, 'D_1.pth')
            # An uncommitted newer file must not override the complete pair.
            torch.save({'iteration': 99}, directory / 'G_99.pth')
            self.assertEqual(Path(checkpoint_path(directory, 'G')).name, 'G_1.pth')
            save_pair(directory, generator, discriminator, optim_g, optim_d, .001, 3)
            history = json.loads((directory / 'studio-checkpoints.json').read_text())
            self.assertEqual(len(history['pairs']), 2)
            self.assertEqual(Path(checkpoint_path(directory, 'G')).name, 'G_3.pth')

    def test_legacy_mismatched_epochs_are_not_resumed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            torch.save({'iteration': 2}, directory / 'G_2333333.pth')
            torch.save({'iteration': 1}, directory / 'D_2333333.pth')
            with self.assertRaises(FileNotFoundError):
                checkpoint_path(directory, 'G')


if __name__ == '__main__':
    unittest.main()
