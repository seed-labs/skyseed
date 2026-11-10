"""Inspect Scene structure, initial orbit geometry and ground locations."""
import argparse
from datetime import datetime, timezone
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


def demo_scene(epoch):
    scene = Scene(data={'description': 'Small shared inspection scene'})
    scene.create_constellation('demo', planes=4, satellites_per_plane=16,
                               altitude_km=550, inclination_deg=53, epoch=epoch)
    scene.create_ground_station_group('ground')
    for name, lat, lon in (('Beijing', 40, 116), ('Quito', 0, -78.5), ('London', 51.5, 0)):
        scene.create_ground_station(name, latitude_deg=lat, longitude_deg=lon, group='ground')
    return scene


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scene', type=Path, help='saved Scene; omit for the small inspection scene')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    scene = Scene.load(args.scene) if args.scene else demo_scene(datetime(2026, 1, 1, tzinfo=timezone.utc))
    begun = perf_counter()
    data = inspection_data(scene)
    calculated = perf_counter()
    path = write_inspection(data, args.output)
    print(f'{path.resolve()}\n{len(scene.nodes)} nodes, {len(data["geometry"]["orbits"])} orbit outlines; '
          f'prepare {calculated-begun:.3f}s; write {perf_counter()-calculated:.3f}s')


if __name__ == '__main__':
    main()
