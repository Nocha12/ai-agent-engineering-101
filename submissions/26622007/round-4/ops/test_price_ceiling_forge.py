import json
import subprocess
import sys
from pathlib import Path

import pytest

from price_ceiling_forge import forge_move


OPS = Path(__file__).resolve().parent
RUNNER = OPS.parents[3] / 'scripts' / 'round4' / 'forge_runner.py'


@pytest.mark.parametrize('mode,args,expected', [
    ('test', {}, True), ('call', {'sku': 'G00b8acf0', 'sell': 600, 'cost': 200}, True),
    ('call', {'sku': '../../not-a-sku'}, False), ('call', {'sku': 'G00b8acf0', 'unsold': 2}, False)])
def test_forge_move_in_unregistered_v51_runner(tmp_path, mode, args, expected):
    move = forge_move()
    definition = move['args']
    assert len(json.dumps(move, ensure_ascii=False)) < 2000
    assert move['action'] == 'forge_tool' and definition['name'] == 'price_ceiling' and definition['permission'] == {}
    for key in ('logic', 'test'):
        (tmp_path / (key + '.py')).write_text(definition[key], encoding='utf8')
    (tmp_path / 'permission.yml').write_text('permission:\n', encoding='utf8')
    request = json.dumps({'root': str(OPS.parent), 'args': args})
    proc = subprocess.run([sys.executable, '-I', '-B', str(RUNNER), str(tmp_path), mode], cwd=tmp_path,
                          input=request, capture_output=True, text=True, timeout=10)
    result = json.loads(proc.stdout.split('@@forge-result@@ ')[-1])
    assert result['ok'] is expected, result
    if expected and mode == 'call':
        assert result['result']['verdict'] == 'skip'
