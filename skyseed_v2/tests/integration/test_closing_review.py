"""Review reproductions: real SIGINT and readers crossing request publications."""
from datetime import timedelta
import json
import os
from pathlib import Path
from queue import Queue
import signal
import subprocess
import sys
from threading import Thread

import pytest
from examples.Sky00_tiny_network import tiny_network as tiny
from skyseed_v2.experiment import ExperimentController
from tests.integration.test_retained_workflow import await_state


@pytest.mark.parametrize('window', ['initial', 'continuation', 'presentation'])
def test_sigint_keeps_accepted_work_owned_until_actual_exit(tmp_path, window):
    script = r'''
import json, os, signal, sys
from pathlib import Path
from datetime import timedelta
from threading import Lock, current_thread, main_thread
from examples.Sky00_tiny_network import tiny_network as tiny
from skyseed_v2.experiment import ExperimentController
import skyseed_v2.experiment.controller as module
root, window = Path(sys.argv[1]), sys.argv[2]
events = os.fdopen(int(sys.argv[3]), 'w')
event_lock = Lock()
def notify(event, **fields):
    with event_lock:
        print(json.dumps({'event': event, **fields}), file=events, flush=True)
real_event = module.Event
class CompletionEvent(real_event):
    def wait(self, *a, **kw):
        if current_thread() is main_thread() and controller._stop.is_set() and not self.is_set():
            notify('DRAINING')
        return super().wait(*a, **kw)
module.Event = CompletionEvent
network=tiny.build_emulator(); simulation=tiny.create_simulator(network,duration=timedelta(seconds=20))
model=simulation.get_physical_link_model(); compute=type(model).compute_observation
blocked=False
def gate():
    notify('BLOCKED')
    assert sys.stdin.readline().strip() == 'release'
    notify('RELEASED')
def physical(self,world,frame,request):
    global blocked
    target=10 if window=='initial' else 30
    if window!='presentation' and not blocked and (frame.timestamp-tiny.DEFAULT_START_TIME).total_seconds()==target:
        blocked=True;gate()
    return compute(self,world,frame,request)
type(model).compute_observation=physical
controller=ExperimentController(network,simulation,root,entry_path=Path(tiny.__file__),through='planner',traffic_enabled=False)
def present(*args):
    if window == 'presentation':gate()
    else:notify('COMPLETED')
try:
    controller.serve(on_completed=present if window != 'initial' else None)
except KeyboardInterrupt:
    notify('RETURNED', status=controller.status())
else:
    raise AssertionError('SIGINT did not propagate')
'''
    event_read, event_write = os.pipe()
    with os.fdopen(event_read) as events, (tmp_path/'stdout').open('w') as output, (tmp_path/'stderr').open('w') as errors:
        try:
            process = subprocess.Popen([sys.executable, '-u', '-c', script, str(tmp_path/'output'), window, str(event_write)],
                pass_fds=(event_write,), stdin=subprocess.PIPE, stdout=output, stderr=errors, text=True, bufsize=1)
        finally:
            # Only the child retains the write end, so EOF means its actual exit.
            os.close(event_write)
        lines = []
        received = Queue()
        def read_lines():
            for line in events:received.put(line)
            received.put(None)
        reader = Thread(target=read_lines,daemon=True)
        reader.start()
        def receive(event):
            # The timeout detects a broken test; cleanup releases the work instead of killing it.
            while True:
                line = received.get(timeout=30); lines.append(line)
                assert line is not None, (process.poll(), lines, (tmp_path/'stdout').read_text(), (tmp_path/'stderr').read_text())
                if not line.strip():continue
                message = json.loads(line)
                if message['event'] == event:return message
        try:
            if window == 'continuation':
                from skyseed_v2.experiment._control import _request
                receive('COMPLETED')
                _request(tmp_path/'output', 'computation', 'continue', seconds=20)
            receive('BLOCKED')
            process.send_signal(signal.SIGINT)
            receive('DRAINING')
            assert process.poll() is None
            process.send_signal(signal.SIGINT)
            receive('DRAINING')
            assert process.poll() is None
            process.stdin.write('release\n'); process.stdin.flush()
            result=receive('RETURNED')['status']
            assert result['producer']['closed'] is True
            assert result['state'] not in {'COMPUTING','IDLE'}
        finally:
            if process.poll() is None:
                try:process.stdin.write('release\n');process.stdin.flush()
                except BrokenPipeError:pass
                from skyseed_v2.experiment._control import _request
                try:_request(tmp_path/'output','computation','cancel-computation')
                except Exception:pass
            process.wait()
            reader.join()
        assert process.returncode == 0, (lines,(tmp_path/'stderr').read_text())


def test_network_export_pins_advancing_publication_and_ignores_stale_view(tmp_path, monkeypatch):
    from tools.observation.physical_world import write_physical_world_tool
    from tools.observation.planner import export
    network=tiny.build_emulator(); simulation=tiny.create_simulator(network,duration=timedelta(seconds=20))
    output=tmp_path/'output'
    controller=ExperimentController(network,simulation,output,entry_path=Path(tiny.__file__),through='planner',traffic_enabled=False)
    errors=[]
    def serve():
        try:controller.serve()
        except BaseException as error:errors.append(error)
    thread=Thread(target=serve);thread.start()
    try:
        await_state(lambda: controller.status()['state']=='IDLE' or errors)
        assert not errors
        write_physical_world_tool(simulation.get_physical_world(),output/'visualizations/physical-world',
            physical_frames_path=output/'results/physical-world/physical-frames.jsonl')
        original=export._read_domain_history_manifest
        def advance(*a,**kw):
            manifest=original(*a,**kw)
            controller.continue_calculation(additional_duration=timedelta(seconds=20))
            await_state(lambda: controller.status()['completed_end'].endswith('00:00:40+00:00') or errors)
            assert not errors
            return manifest
        monkeypatch.setattr(export,'_read_domain_history_manifest',advance)
        first=export.write_network_export(output,tmp_path/'first')
        assert first['formal_frames']==3
        monkeypatch.setattr(export,'_read_domain_history_manifest',original)
        second=export.write_network_export(output,tmp_path/'second')
        assert second['formal_frames']==5  # PhysicalWorld page still has only three.
    finally:
        controller.cancel_computation();thread.join()
    assert not errors


def test_generated_performance_invocation_pins_recursive_generation_read(tmp_path):
    from skyseed_v2.experiment.execution import _write_generated_run_script
    network=tiny.build_emulator(); simulation=tiny.create_simulator(network)
    output=ExperimentController(network,simulation,tmp_path/'output',entry_path=Path(tiny.__file__),
        traffic_enabled=False,internet_map_enabled=False).generate()
    generation=output/'results/experiment/generation-performance.jsonl'
    with generation.open('a') as stream:stream.write('{unfinished suffix')
    driver=tmp_path/'python-driver'
    driver.write_text(f'''#!{sys.executable}
import json, os, sys
from pathlib import Path
args=sys.argv[1:]
if args[:2]==['-m','skyseed_v2.experiment.execution']:
    directory=Path(args[args.index('--invocation-directory')+1])/'performance'
    directory.mkdir(parents=True,exist_ok=True)
    generation=Path(args[2])/'results/experiment/generation-performance.jsonl'
    (directory/'execution-observations.jsonl').write_text(json.dumps({{'record_type':'execution_observation_header','format_version':2,'metadata':{{'generation_reference':os.path.relpath(generation,directory)}}}})+'\\n')
elif args[:2]==['-m','tools.observation.performance']:
    os.execv(sys.executable,[sys.executable,*args])
''');driver.chmod(0o755)
    _write_generated_run_script(output,python_executable=str(driver),tool_package_parent=Path(tiny.__file__).resolve().parents[2])
    completed=subprocess.run(['bash',str(output/'run.sh')],capture_output=True,text=True)
    assert completed.returncode==0,(completed.stdout,completed.stderr)
    assert 'detached inspection Tools failed' not in completed.stderr
    assert (output/'visualizations/performance/index.html').is_file()
