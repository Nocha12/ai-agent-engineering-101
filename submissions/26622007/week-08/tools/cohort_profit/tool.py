import json
import os
import sys

import profit_cohorts


def main():
    args = json.load(sys.stdin)
    root = os.path.dirname(os.path.abspath(os.environ['MARKET_DIR']))
    out = profit_cohorts.build(root)
    if args.get('sku'):
        out['skus'] = {args['sku']: out['skus'].get(args['sku'])}
    else:
        out['skus'] = dict(sorted(out['skus'].items(),
                                 key=lambda kv: -kv[1]['all']['paid'])[:5])
    print(json.dumps(out, ensure_ascii=False))


if __name__ == '__main__':
    main()
