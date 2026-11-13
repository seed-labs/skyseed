"""Write the two offline inspectors with shared drawing assets, without models."""
import json
from pathlib import Path


def write_page(data, output, *, template, data_name):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    assets = Path(__file__).parent
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False)
    coastline = (assets / 'assets/natural_earth_110m_coastline.json').read_text()
    page = Path(template).read_text(encoding='utf-8')
    # Inline assets keep the exported page usable without a server or CDN.
    replacements = {
        '/*INSPECTION_STYLE*/': (assets / 'inspection.css').read_text(),
        '/*SPATIAL_VIEW*/': (assets / 'spatial_view.js').read_text(),
        '/*COASTLINE_DATA*/null': coastline,
        '/*INSPECTION_DATA*/null': payload.replace('<', '\\u003c'),
    }
    for marker, value in replacements.items():
        page = page.replace(marker, value)
    (output / f'{data_name}.json').write_text(payload + '\n', encoding='utf-8')
    (output / 'index.html').write_text(page, encoding='utf-8')
    return output / 'index.html'
