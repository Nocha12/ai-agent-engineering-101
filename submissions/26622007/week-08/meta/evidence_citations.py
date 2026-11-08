"""Make exact file-line citations after a producer has exited; never guess an OK line."""
import argparse
import json
from pathlib import Path


def cite(path, text, root=None):
    root = Path(root or Path(__file__).resolve().parents[1]).resolve()
    path = (root / path).resolve()
    relative = path.relative_to(root)
    if len(text.strip()) < 4:
        raise ValueError('excerpt must contain at least four characters')
    # Match verify_evidence's iteration, including its newline handling.
    with path.open(encoding='utf-8', errors='replace') as source:
        matches = [{'ref': f'{relative}:{n}', 'excerpt': line.rstrip('\n'),
                    'meaning': '종료된 실행의 실제 행; 제출 직전 verify_evidence로 재검증'}
                   for n, line in enumerate(source, 1) if text in line]
    if not matches:
        raise ValueError('no matching line; wait for producer completion or correct the excerpt')
    return matches


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path')
    parser.add_argument('text')
    args = parser.parse_args()
    print(json.dumps(cite(args.path, args.text), ensure_ascii=False, indent=1))
