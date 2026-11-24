"""Print the small v5.1 forge_tool request; never installs it."""

import json
from pathlib import Path


def forge_move():
    template = Path(__file__).with_name('forge_templates') / 'price_ceiling'
    meta = dict(line.split(': ', 1) for line in (template / 'permission.yml').read_text(encoding='utf8').splitlines() if ': ' in line)
    return {'action': 'forge_tool', 'args': {
        'name': meta['name'], 'description': meta['description'], 'permission': {},
        'logic': (template / 'logic.py').read_text(encoding='utf8'),
        'test': (template / 'test.py').read_text(encoding='utf8')}}


if __name__ == '__main__':
    print(json.dumps(forge_move(), ensure_ascii=False))
