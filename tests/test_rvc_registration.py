from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import faiss
import numpy as np

from app.yue2_app.rvc_library import register_voice, list_voices, rename_voice


class RegistrationRecovery(unittest.TestCase):
    def test_same_export_is_reused_and_new_weights_keep_old_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = root/'model.pth'
            model.write_bytes(b'first trained weights')
            index = faiss.IndexFlatL2(4)
            index.add(np.ones((2,4),dtype=np.float32))
            index_path = root/'trained.index'
            faiss.write_index(index,str(index_path))
            metadata = {'speakers':[{'id':0,'name':'default'}],'feature_dimensions':4}
            with patch('app.yue2_app.rvc_library.inspect_checkpoint',return_value=metadata):
                first = register_voice(root,model,{0:index_path},name='Project',project_id='project',reuse_project=True)
                rename_voice(root,first['id'],'My renamed voice')
                resumed = register_voice(root,model,{0:index_path},name='Project',project_id='project',reuse_project=True)
                self.assertEqual(first['id'],resumed['id'])
                self.assertEqual(resumed['name'],'My renamed voice')
                self.assertEqual(len(list_voices(root)),1)
                model.write_bytes(b'new trained weights')
                later = register_voice(root,model,{0:index_path},name='Project',project_id='project',reuse_project=True)
                self.assertNotEqual(first['id'],later['id'])
                self.assertEqual(len(list_voices(root)),2)
                self.assertEqual(Path(first['model_path']).read_bytes(),b'first trained weights')
                Path(later['model_path']).write_bytes(b'corrupt')
                recovered = register_voice(root,model,{0:index_path},name='Project',project_id='project',reuse_project=True)
                self.assertNotEqual(later['id'],recovered['id'])
                self.assertEqual(Path(recovered['model_path']).read_bytes(),b'new trained weights')


if __name__=='__main__':
    unittest.main()
