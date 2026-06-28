from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Dict, List


ITEM_TYPES = {
    "small": {
        "w": 760,
        "l": 1130,
        "h": 550,
        "weight_range": (100, 400),
    },
    "medium": {
        "w": 1490,
        "l": 2260,
        "h": 900,
        "weight_range": (500, 1500),
    },
    "large": {
        "w": 2280,
        "l": 2550,
        "h": 2355,
        "weight_range": (1500, 4000),
    },
}

DESTINATIONS = ["DEST_A", "DEST_B", "DEST_C"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate fixed-format packing input json")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=Path("items_input.json"))
    return parser.parse_args()


def generate_items(count: int = 100, seed: int = 42) -> Dict[str, object]:
    rng = random.Random(seed)
    size_types = list(ITEM_TYPES.keys())
    items: List[Dict[str, object]] = []

    for index in range(1, count + 1):
        size_type = rng.choice(size_types)
        spec = ITEM_TYPES[size_type]
        weight = round(rng.uniform(*spec["weight_range"]), 2)
        destination_id = rng.choice(DESTINATIONS)

        items.append(
            {
                "item_id": f"P{index:03d}",
                "size_type": size_type,
                "dimensions": {
                    "w": spec["w"],
                    "l": spec["l"],
                    "h": spec["h"],
                },
                "weight": weight,
                "destination_id": destination_id,
            }
        )

    return {
        "dataset_info": {
            "dataset_name": "case_01",
            "seed": seed,
            "item_count": count,
        },
        "items": items,
    }


def write_items(data: Dict[str, object], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main() -> None:
    args = parse_args()
    data = generate_items(count=args.count, seed=args.seed)
    write_items(data, args.output)
    print(f"generated_file={args.output.resolve()}")
    print(f"dataset_name={data['dataset_info']['dataset_name']}")
    print(f"item_count={data['dataset_info']['item_count']}")
    print(f"seed={args.seed}")


if __name__ == "__main__":
    main()
