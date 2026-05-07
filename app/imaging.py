from pathlib import Path
from PIL import Image, ImageOps
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:
    pass

SUPPORTED = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tif', '.tiff', '.webp', '.heic', '.heif'}

def safe_open(path: str, max_pixels: int):
    Image.MAX_IMAGE_PIXELS = max_pixels
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    return im

def make_derivatives(path: str, image_id: int, thumb_dir: str, analysis_dir: str, max_thumb: int, max_side: int, max_pixels: int):
    p = Path(path)
    Path(thumb_dir).mkdir(parents=True, exist_ok=True)
    Path(analysis_dir).mkdir(parents=True, exist_ok=True)
    with safe_open(str(p), max_pixels) as im:
        width, height = im.size
        rgb = im.convert('RGB')
        thumb = rgb.copy()
        thumb.thumbnail((max_thumb, max_thumb))
        thumb_path = Path(thumb_dir) / f"{image_id}.jpg"
        thumb.save(thumb_path, 'JPEG', quality=82, optimize=True)
        analysis = rgb.copy()
        analysis.thumbnail((max_side, max_side))
        analysis_path = Path(analysis_dir) / f"{image_id}.jpg"
        analysis.save(analysis_path, 'JPEG', quality=90, optimize=True)
    return width, height, str(thumb_path), str(analysis_path)
