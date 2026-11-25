"""Inspect Scene structure, initial orbit geometry and ground locations."""
import argparse
from pathlib import Path
from time import perf_counter

from skyseed import Scene
from tools.observation._presentation.inspection_page import write_page
from tools.observation._presentation.scene_display import scene_inputs, static_geometry


def inspection_data(scene):
    inputs = scene_inputs(scene)
    return {'format': 'skyseed.scene-inspection/v2', **inputs,
            'geometry': static_geometry(inputs)}


def write_inspection(data, output):
    return write_page(data, output, template=Path(__file__).with_name('viewer.html'),
                      data_name='scene')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', type=Path, required=True, help='saved Scene')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    scene = Scene.load(args.scene)
    begun = perf_counter()
    data = inspection_data(scene)
    calculated = perf_counter()
    path = write_inspection(data, args.output)
    print(f'{path.resolve()}\n{len(scene.nodes)} nodes, {len(data["geometry"]["orbits"])} orbit outlines; '
          f'prepare {calculated-begun:.3f}s; write {perf_counter()-calculated:.3f}s')


if __name__ == '__main__':
    main()
