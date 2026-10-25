import base64
import io
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image

import ocr_service


class OcrTests(unittest.TestCase):
    def test_company_candidates(self):
        names = ocr_service.company_candidates('公司名称：腾讯科技（深圳）有限公司\n腾讯科技（深圳）有限公司\n小米集团\n公司名称：腾讯有限公司')
        self.assertEqual(names, ['腾讯科技（深圳）有限公司', '小米集团', '腾讯有限公司'])
        self.assertEqual(ocr_service.company_candidates('普通截图，没有企业名称'), [])
        self.assertEqual(ocr_service.company_candidates('腾讯的游戏\nMicrosoft'), ['microsoft', '腾讯'])

    def picture(self, fmt='PNG'):
        buf = io.BytesIO()
        Image.new('RGB', (100, 60), 'white').save(buf, format=fmt)
        return {'image': base64.b64encode(buf.getvalue()).decode()}

    def test_invalid_inputs(self):
        for value in (None, 12, '', 'bad-base64', base64.b64encode(b'not an image').decode()):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ocr_service.recognize({'image': value})

    @patch.object(ocr_service, 'capability', return_value={'available': False})
    def test_unavailable(self, _):
        with self.assertRaisesRegex(ValueError, '未就绪'):
            ocr_service.recognize(self.picture())

    @patch.object(ocr_service, 'capability', return_value={'available': True, 'engine': 'Tesseract'})
    @patch.object(ocr_service.shutil, 'which', return_value='/usr/bin/tesseract')
    def test_formats_and_cleanup(self, *_):
        paths = []
        def run(command, **kwargs):
            paths.append(Path(command[1]))
            self.assertTrue(paths[-1].exists())
            self.assertEqual(command[-1], '11')
            return subprocess.CompletedProcess(command, 0, '腾讯 Tencent'.encode())
        with patch.object(ocr_service.subprocess, 'run', side_effect=run):
            for fmt in ('PNG', 'JPEG'):
                self.assertEqual(ocr_service.recognize(self.picture(fmt)), '腾讯 Tencent')
        self.assertTrue(all(not p.exists() for p in paths))

    @patch.object(ocr_service, 'capability', return_value={'available': True, 'engine': 'Tesseract'})
    def test_malformed(self, _):
        with self.assertRaisesRegex(ValueError, '图片损坏'):
            ocr_service.recognize({'image': base64.b64encode(b'\x89PNG\r\n\x1a\ninvalid').decode()})

    @patch.object(ocr_service, 'capability', return_value={'available': True, 'engine': 'Tesseract'})
    @patch.object(ocr_service.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ocr', 45))
    def test_timeout(self, *_):
        with self.assertRaisesRegex(ValueError, '超时'):
            ocr_service.recognize(self.picture())

    @patch.object(ocr_service, 'capability', return_value={'available': True, 'engine': 'Tesseract'})
    def test_dimensions(self, _):
        buf = io.BytesIO()
        Image.new('RGB', (12001, 1)).save(buf, format='PNG')
        with self.assertRaisesRegex(ValueError, '尺寸过大'):
            ocr_service.recognize({'image': base64.b64encode(buf.getvalue()).decode()})
