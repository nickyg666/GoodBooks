import sys
sys.path.insert(0, '.')
from gb_lens import identify_cover
import requests
img_url = 'https://covers.openlibrary.org/b/isbn/0451524934-L.jpg'
img_data = requests.get(img_url).content
print('Image length:', len(img_data))
try:
    res = identify_cover(img_data, timeout=30)
    print('Lens result:', res)
except Exception as e:
    print('Error:', e)
