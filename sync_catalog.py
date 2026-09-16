"""Read public Apple product metadata; never infer part numbers."""
import gzip
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COLORS = {
    'Black': '黑色',
    'Silver': '银色',
    'Burgundy': '勃艮第酒红色',
    'Glacier Blue': '冰川蓝色',
    'Glacier': '冰川蓝色',
    'Midnight': '夜空色',
    'Night Sky': '夜空色',
    'Starlight': '星光白色',
    'Star White': '星光白色',
}


def catalog():
    rows = {}
    for family in ('iphone-18-pro', 'iphone-duo'):
        url = 'https://www.apple.com.cn/shop/buy-iphone/' + family
        with urllib.request.urlopen(url, timeout=25) as response:
            body = response.read()
        if body[:2] == b'\x1f\x8b':
            body = gzip.decompress(body)
        html = body.decode('utf-8')
        pattern = r'\{"sku":"[^"]+","partNumber":"[^"]+","price":\{"fullPrice":[0-9.]+\},"category":"iphone","name":"[^"]+"\}'
        products = [json.loads(m.group()) for m in re.finditer(pattern, html)]
        if not products:
            raise RuntimeError(f'{family} 商品目录未能解析，保留原有配置。')
        for product in products:
            name = product['name'].replace('\xa0', ' ')
            for english, chinese in COLORS.items():
                name = name.replace(english, chinese)
            part = product['partNumber']
            rows[part] = dict(name=name, part=part, price=product['price']['fullPrice'], url=url + '/' + part.lower(), source=url)
    return list(rows.values())


if __name__ == '__main__':
    rows = catalog()
    (ROOT / 'catalog.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'已同步 {len(rows)} 个真实商品配置。')
