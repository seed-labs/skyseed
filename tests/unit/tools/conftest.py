"""Build saved drawing inputs for the plan-inspector behavior tests."""
import json
import pytest
from skyseed.pc.link import LinkPlan
from tools.observation.plan_views import save_context


@pytest.fixture
def inspect_plans(tmp_path):
    def inspect(module, physical, *histories, evaluations=(), timestamps=()):
        links, routes = [], []
        for history in histories:
            for plan in history:
                (links if isinstance(plan, LinkPlan) else routes).append(plan)
        is_link = not routes
        path = save_context(tmp_path / 'context.json', physical, link_plans=links, route_plans=routes,
                            link_evaluations=evaluations if is_link else (),
                            route_evaluations=() if is_link else evaluations, position_times=timestamps)
        context = json.loads(path.read_text())
        return module.inspection_data(physical.scene, *histories, positions=context['positions'],
                                      evaluations=context['link_evaluations' if is_link else 'route_evaluations'],
                                      timestamps=timestamps)
    return inspect
