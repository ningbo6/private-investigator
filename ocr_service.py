"""Local-only screenshot extraction; never calls search or a remote model."""
import base64
import binascii
import io
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import warnings
from functools import lru_cache

ROOT = Path(__file__).resolve().parent


def company_candidates(text):
    """Conservative suggestions, never entity verification or automatic search."""
    from scope_guard import BRANDS, check
    candidates = []
    for line in text.splitlines():
        line = re.sub(r'^(?:公司名称|企业名称|名称|申请人)\s*[:：]\s*', '', line.strip())
        # OCR often inserts spaces between Chinese characters.
        line = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', line)
        for part in re.split(r'[，,。；;：:|\t]', line):
            part = part.strip()
            if (re.fullmatch(r'[\u4e00-\u9fffA-Za-z0-9（）()· &.-]{2,80}(?:有限公司|股份公司|集团)', part)
                    or re.fullmatch(r'[A-Za-z][A-Za-z0-9 &.,()-]{2,80}\b(?:Inc\.?|Ltd\.?|Limited|Corporation)', part, re.I)):
                try:
                    check(part, '', [])
                except ValueError:
                    continue
                candidates.append(part)
    for brand in sorted(BRANDS, key=lambda x: (-len(x), x)):
        pattern = re.escape(brand) if not brand.isascii() else r'\b' + re.escape(brand) + r'\b'
        if re.search(pattern, text, re.I) and not any(brand.lower() in name.lower() for name in candidates):
            candidates.append(brand)
    return list(dict.fromkeys(candidates))[:12]


@lru_cache(maxsize=1)
def capability():
    binary = ROOT / 'tools/ocr_image'
    if sys.platform == 'darwin' and os.access(binary, os.X_OK):
        return {'available': True, 'engine': 'Apple Vision', 'languages': '简体中文 / English'}
    try:
        from PIL import Image
        binary = shutil.which('tesseract')
        if binary:
            result = subprocess.run([binary, '--list-langs'], capture_output=True, timeout=5, check=True)
            if {'chi_sim', 'eng'} <= set(result.stdout.decode().split()):
                return {'available': True, 'engine': 'Tesseract', 'languages': '简体中文 / English'}
    except (ImportError, OSError, subprocess.SubprocessError):
        pass
    return {'available': False, 'engine': None, 'languages': ''}


def recognize(body):
    encoded = body.get('image')
    if not isinstance(encoded, str) or not 0 < len(encoded) <= 6_666_668:
        raise ValueError('请选择 5 MB 以下的 PNG/JPEG 图片')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError('图片编码无效，请重新选择 PNG/JPEG 图片') from None
    if len(raw) > 5_000_000 or not raw.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff')):
        raise ValueError('仅支持 5 MB 以下的 PNG/JPEG 图片')
    backend = capability()
    if not backend['available']:
        raise ValueError('本地 OCR 未就绪，请重新构建 Docker 镜像或手动输入公司名称')
    with tempfile.TemporaryDirectory(prefix='company-ocr-') as directory:
        path = Path(directory) / 'input.png'
        if backend['engine'] == 'Apple Vision':
            path.write_bytes(raw)
            command = [str(ROOT / 'tools/ocr_image'), str(path)]
        else:
            from PIL import Image, ImageOps, UnidentifiedImageError
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter('error', Image.DecompressionBombWarning)
                    with Image.open(io.BytesIO(raw)) as source:
                        w, h = source.size
                        if source.format not in ('PNG', 'JPEG') or max(w, h) > 12000 or w * h > 30_000_000:
                            raise ValueError('图片尺寸过大，请裁剪到 12000 边长、3000 万像素以内')
                        source.load()
                        frame = ImageOps.exif_transpose(source).convert('RGBA')
                        background = Image.new('RGBA', frame.size, 'white')
                        background.alpha_composite(frame)
                        background.convert('RGB').save(path)
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
                raise ValueError('图片损坏或尺寸过大，请重新选择清晰的 PNG/JPEG 截图') from None
            command = [shutil.which('tesseract'), str(path), 'stdout', '-l', 'chi_sim+eng', '--psm', '11']
        try:
            result = subprocess.run(command, capture_output=True, timeout=45, check=True,
                                    env={**os.environ, 'OMP_THREAD_LIMIT': '2'})
        except subprocess.TimeoutExpired:
            raise ValueError('OCR 识别超时，请裁剪截图后重试') from None
        except (OSError, subprocess.CalledProcessError):
            raise ValueError('OCR 未能解析图片，请换一张清晰截图重试') from None
        return result.stdout.decode('utf-8', errors='replace').strip()[:12000]
