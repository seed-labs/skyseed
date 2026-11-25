"""Inspect saved PhysicalPlans without running planners or backend operations."""
import argparse
from pathlib import Path

from skyseed import load_plans
from skyseed.pc.compiler import PhysicalPlan
from tools.observation._presentation.inspection_page import write_page
from tools.observation._presentation.plan_details import _field_values
from .instruction_display import operation_views


def inspection_data(plans):
    records = []
    for plan in plans:
        if not isinstance(plan, PhysicalPlan):
            raise TypeError("compiler inspection requires saved PhysicalPlans")
        record = _field_values(plan)
        record["views"] = operation_views(record)
        records.append(record)
    return {"format": "skyseed.compilation-inspection/v1", "plans": records}


def write_inspection(plans, output):
    data = inspection_data(plans)
    return write_page(data, output, template=Path(__file__).with_name("viewer.html"), data_name="compilation")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plans", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plans = (plan for path in args.plans for plan in load_plans(path))
    print(write_inspection(plans, args.output))


if __name__ == "__main__":
    main()
