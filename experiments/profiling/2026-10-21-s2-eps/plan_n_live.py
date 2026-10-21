"""Regenerate plan live-column counts on the server with the project environment."""
import csv
import pickle
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from utility.lifetime import max_live_columns  # noqa: E402


def main():
    rows = []
    for qid in range(1, 23):
        with (ROOT / f'queries/pickles/planobj-q{qid}.pkl').open('rb') as stream:
            plans = pickle.load(stream)
        rows.append(dict(qid=qid,
                         n_live_free_dead=max(max_live_columns(p.ops_idx, p.ops_dict, True) for p in plans),
                         n_live_no_free=max(max_live_columns(p.ops_idx, p.ops_dict, False) for p in plans)))
    with (HERE / 'plan_n_live.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    print(f'Wrote {len(rows)} queries to {HERE / "plan_n_live.csv"}')


if __name__ == '__main__':
    main()
