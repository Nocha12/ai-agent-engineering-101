from runpy import run_path


def run(args, kit):
    return run_path('ops/price_ceiling.py')['quote'](args)
