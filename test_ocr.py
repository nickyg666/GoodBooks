import sys
sys.path.insert(0, '.')
from gb_lens import extract_ocr_text
import requests
img_url = 'https://covers.openlibrary.org/b/isbn/0451524934-L.jpg'
img_data = requests.get(img_url).content
print('Image bytes:', len(img_data))
text = extract_ocr_text(img_data)
print('OCR text (first 200 chars):', repr(text[:200]) if text else 'None or empty')
