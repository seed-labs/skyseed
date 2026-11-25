"""Apply prepared PhysicalPlans on one monotonic clock."""
from collections import deque
from bisect import insort
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from pathlib import Path
from queue import Queue, Empty
from threading import Thread
import time

from ..pc.compiler.bpf.compile_program import compile_label_program
from .deployment import Deployment
from .execution_process import CommandChannel
from .plan_queue import PlanQueue, instruction_record


class _Journal:
    """Observation IO cannot occupy the scheduled execution channel."""
    def __init__(self, path, store):
        self.path, self.store = path, store
        self.queue = Queue()
        self.error = None
        self.stream = self.path.open("x")
        self.thread = Thread(target=self._write, name="skyseed-observation")
        self.thread.start()

    def append(self, record):
        self.queue.put(record)

    def _write(self):
        try:
            with self.stream as output:
                sequence = 0
                store = self.store
                stopping = False
                while True:
                    record = self.queue.get()
                    if record is None:
                        break
                    batch = [record]
                    while len(batch) < 64:
                        try:
                            record = self.queue.get_nowait()
                        except Empty:
                            break
                        if record is None:
                            stopping = True
                            break
                        batch.append(record)
                    values = {}
                    for record in batch:
                        output.write(json.dumps(record, separators=(",", ":")) + "\n")
                        values[f"runner/events/{sequence:09d}"] = record
                        sequence += 1
                    output.flush()
                    if store is not None:
                        try:
                            store.put_many(values)
                        except BaseException as error:
                            self.error = error
                            store = None  # Preserve the accepted file records even if etcd fails.
                    if stopping:
                        break
        except BaseException as error:
            self.error = error

    def close(self):
        self.queue.put(None)
        self.thread.join()
        if self.error is not None:
            raise self.error


class Runner:
    """Prepare, run once on a monotonic clock, and explicitly release resources.

    Deployment lifecycle stays with the caller. run() drains the accepted plan
    interval and leaves the last network state in place until close(). stop()
    ends scheduling early after the current call, retaining actual receipts.
    """
    def __init__(self, plans, deployment, *, output, store=None, program=None):
        self.deployment = deployment if isinstance(deployment, Deployment) else Deployment(deployment)
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.store, self.program = store, program
        self._plans = iter(plans)
        self._channel = self._journal = self._loader = self._next = None
        self._pending = []
        self._plan_queue = PlanQueue()
        self._maintenance = deque()
        self._node_jobs = deque()
        self._busy_nodes = set()
        self._publication = None
        self._nodes = {}
        self._prefixes = set()
        self._serial = 0
        self.origin = self.start_time = self.backend = None
        self._prepared = self._ran = self._exhausted = False
        self._stop_requested = False

    def _record(self, kind, **fields):
        self._journal.append({"kind": kind, **fields})

    def _read(self):
        return next(self._plans, None)

    def _receive(self, horizon):
        """Decode ahead independently; an unavailable successor blocks no known work."""
        if self._next is not None and self._next.done():
            plan = self._next.result()
            if plan is None:
                self._plan_queue.finish()
                self._exhausted = True
                self._next = None
            elif plan.start_time <= horizon:
                self._plan_queue.accept(plan)
                self._next = self._loader.submit(self._read)
        while self._plan_queue.timestamp is not None and self._plan_queue.timestamp <= horizon:
            self._enqueue(self._plan_queue.pop())

    def _known_through(self, timestamp):
        if self._exhausted or self._plan_queue.previous_start >= timestamp:
            return True
        if self._next is not None and self._next.done():
            plan = self._next.result()
            return plan is None or plan.start_time >= timestamp
        return False

    def _enqueue(self, group):
        self._serial += 1
        by_node = {}
        for operation in group.routes:
            by_node.setdefault(operation.target, []).extend(
                instruction_record(item) for item in operation.instructions)
        state = {
            "group": group,
            "id": self._serial,
            "ready": False,
            "links_done": False,
            "remaining": set(by_node),
            "instructions": by_node,
            "versions": {},
            "started_nodes": set(),
            "link_remaining": [
                (operation, [instruction_record(item) for item in operation.instructions])
                for operation in group.links
            ],
        }
        state["work"] = self._prepare_resources(state)
        insort(self._pending, state, key=lambda item: (item["group"].timestamp, item["id"]))

    def _prepare_resources(self, state):
        group = state["group"]
        new_node = False
        for identity, target in group.targets.items():
            if identity in self._nodes:
                if self._nodes[identity] != target:
                    raise ValueError(f"changed physical node target {identity}")
                continue
            container = self._containers[target["service"]]
            labels = container["Config"]["Labels"]
            actual = labels.get("org.seedsecuritylabs.seedemu.meta.skyseed.scene_id")
            if actual != identity or not container["State"]["Running"]:
                raise ValueError(f"deployment does not match planned node {identity}")
            expected = {address for _, address in target["static_addresses"]}
            actual_addresses = {item["IPAddress"] for item in container["NetworkSettings"]["Networks"].values()}
            if not expected <= actual_addresses:
                raise ValueError(f"deployment static addresses differ at {identity}")
            self._channel.request("nodes", targets=[{**target, "pid": container["State"]["Pid"]}])
            self._nodes[identity] = target
            new_node = True
            yield
        changed = not group.prefixes <= self._prefixes
        self._prefixes.update(group.prefixes)
        if self.backend == "bpf" and (new_node or changed):
            self._channel.request("prefixes", prefixes=sorted(self._prefixes))
            yield
        state["ready"] = True
        self._record("available", scheduled=group.timestamp.isoformat(), completed=time.monotonic(),
                     nodes=len(group.targets), link_operations=len(group.links), route_operations=len(group.routes))
        yield

    def prepare(self):
        if self._prepared:
            raise RuntimeError("Runner has already been prepared")
        first = next(self._plans)
        if first.forwarding_backend not in {"bpf", "linux"}:
            raise ValueError(f"unsupported Runner backend {first.forwarding_backend}")
        self.backend = first.forwarding_backend
        self._plan_queue.accept(first)
        self.start_time = first.start_time
        self._journal = _Journal(self.output / "execution.jsonl", self.store)
        begun = time.monotonic()
        try:
            self._loader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="skyseed-plan-reader")
            self._next = self._loader.submit(self._read)
            # Before starting the clock, establish the complete initial group.
            # Only this preparation boundary waits for the next batch start.
            while True:
                following = self._next.result()
                if following is None or following.start_time > self.start_time:
                    break
                self._plan_queue.accept(following)
                self._next = self._loader.submit(self._read)
            self._containers = self.deployment.containers()
            image = next(iter(self._containers.values()))["Config"]["Image"]
            program = self.program
            if self.backend == "bpf" and program is None:
                program = compile_label_program()
            self._channel = CommandChannel(image, self.backend, program, self.output / "helper.log")
            self._enqueue(self._plan_queue.pop())
            self._restore_initial(self._pending.pop(0))
            self._prepared = True
            self._record("ready", started=begun, completed=time.monotonic(), start_time=self.start_time.isoformat())
        except BaseException as error:
            try:
                self.close()
            except BaseException as cleanup:
                error.add_note(f"cleanup also failed: {cleanup}")
            raise
        return self

    def _restore_initial(self, state):
        for _ in state["work"]:
            pass
        self._execute_links(state)
        if self.backend == "bpf" and state["remaining"]:
            instructions = [item for values in state["instructions"].values() for item in values]
            self._channel.request("prepare", job="initial", instructions=instructions)
            while True:
                result = self._channel.request("step", job="initial")["result"]
                if result["done"]:
                    break
            self._publish(result["value"], [(state, set(state["remaining"]))])
        elif self.backend == "linux":
            self._execute_linux(state)


    def _execute_links(self, state):
        waiting = []
        for operation, instructions in state["link_remaining"]:
            if self._stop_requested:
                return
            if not all(node in self._nodes for node in operation.target):
                waiting.append((operation, instructions))
                continue
            response = self._channel.request("apply", instructions=instructions)
            self._record("link", scheduled=operation.timestamp.isoformat(), target=operation.target,
                         operation=operation.kind, started=response["started"],
                         activated=response["completed"], completed=response["completed"])
        state["link_remaining"] = waiting
        state["links_done"] = not waiting

    def _execute_linux(self, state):
        for operation in state["group"].routes:
            if self._stop_requested:
                return
            response = self._channel.request("apply", instructions=[
                instruction_record(item) for item in operation.instructions])
            self._record("route", scheduled=operation.timestamp.isoformat(), target=operation.target,
                         operation=operation.kind, started=response["started"],
                         activated=response["completed"], completed=response["completed"])
        state["remaining"].clear()

    def _prepare_node(self, state, node):
        job = f"table-{state['id']}-{node}"
        self._channel.request("prepare_node", job=job, node=node, instructions=state["instructions"][node])
        yield
        while True:
            result = self._channel.request("step", job=job)["result"]
            if result["done"]:
                state["versions"][node] = result["value"]
                self._record("prepared", scheduled=state["group"].timestamp.isoformat(),
                             node=node, version=result["value"], completed=time.monotonic())
                self._busy_nodes.remove(node)
                return
            yield

    def _start_node_jobs(self):
        earlier = set()
        prepared = {}
        for state in self._pending:
            for node in state["remaining"]:
                if (state["ready"] and node not in earlier and node not in self._busy_nodes
                        and node not in state["started_nodes"] and prepared.get(node, 0) < 2):
                    self._busy_nodes.add(node)
                    state["started_nodes"].add(node)
                    self._node_jobs.append(self._prepare_node(state, node))
                if node in state["started_nodes"]:
                    prepared[node] = prepared.get(node, 0) + 1
                if node not in state["versions"]:
                    earlier.add(node)

    def _choose_publication(self, now):
        if self._publication is not None:
            return
        selected, blocked = [], set()
        for state in self._pending:
            remaining = state["remaining"]
            if not remaining:
                continue
            ready = remaining.intersection(state["versions"]) - blocked
            due = self._due(state)
            if due > now:
                # Prepare the next complete publication early. A late predecessor
                # is not hidden by a future generation.
                if (not blocked and not selected and ready == remaining
                        and self._known_through(state["group"].timestamp)):
                    selected.append((state, ready))
                break
            if ready:
                selected.append((state, ready))
            blocked.update(remaining)
        if selected:
            publication = {"selected": selected, "ready": False,
                           "due": max(self._due(state) for state, _ in selected)}
            publication["work"] = self._prepare_publication(publication)
            self._publication = publication

    def _prepare_publication(self, publication):
        updates = {node: state["versions"][node] for state, nodes in publication["selected"] for node in nodes}
        self._channel.request("generation", job="publication", updates=updates)
        yield
        while True:
            result = self._channel.request("step", job="publication")["result"]
            if result["done"]:
                publication["generation"] = result["value"]
                publication["ready"] = True
                return
            yield

    def _publish(self, generation, selected):
        response = self._channel.request("publish", generation=generation)
        adoptions = []
        for state, nodes in selected:
            state["remaining"].difference_update(nodes)
            ordered_nodes = sorted(nodes)
            counts = {node: len(state["instructions"][node]) for node in ordered_nodes}
            adoptions.append({"scheduled": state["group"].timestamp.isoformat(),
                              "nodes": ordered_nodes, "instruction_counts": counts})
        self._record("route", generation=generation, adoptions=adoptions,
                     started=response["started"], activated=response["result"]["activated"],
                     completed=response["completed"])
        previous = response["result"]["previous"]
        if previous:
            self._maintenance.append(self._reclaim(previous))

    def _reclaim(self, generation):
        job = f"reclaim-{generation}"
        self._channel.request("reclaim", job=job, generation=generation)
        yield
        while not self._channel.request("step", job=job)["result"]["done"]:
            yield

    def _due(self, state):
        return self.origin + (state["group"].timestamp - self.start_time).total_seconds()

    @staticmethod
    def _advance(queue):
        work = queue.popleft()
        try:
            next(work)
            queue.append(work)
        except StopIteration:
            pass

    def stop(self):
        """Request a stop after the current backend call; close() releases resources."""
        self._stop_requested = True

    def run(self):
        if not self._prepared or self._ran:
            raise RuntimeError("prepare one Runner before its single run()")
        self._ran = True
        self.origin = time.monotonic()
        self._record("clock", origin=self.origin, start_time=self.start_time.isoformat())
        turn = 0
        while not self._exhausted or self._plan_queue.pending or self._pending or self._maintenance:
            if self._stop_requested:
                break
            if self._journal.error is not None:
                raise self._journal.error
            horizon = self.start_time + timedelta(seconds=time.monotonic() - self.origin + 30)
            self._receive(horizon)
            now = time.monotonic()
            for state in self._pending:
                if self._stop_requested or self._due(state) > now:
                    break
                if not state["links_done"]:
                    self._execute_links(state)
                if self.backend == "linux" and state["ready"] and state["remaining"]:
                    self._execute_linux(state)
            if self._stop_requested:
                continue
            publication = self._publication
            if publication and publication["ready"] and publication["due"] <= time.monotonic():
                self._publish(publication["generation"], publication["selected"])
                self._publication = None
            if self._stop_requested:
                continue
            self._pending = [state for state in self._pending
                             if not (state["links_done"] and state["ready"] and not state["remaining"])]
            if self.backend == "bpf":
                self._start_node_jobs()
                self._choose_publication(time.monotonic())
            # Fair bounded work: table writes cannot starve retirement, and a
            # large node table cannot block another node's ready update.
            work = []
            resource = next((state for state in self._pending if not state["ready"]), None)
            if resource:
                work.append(("single", resource["work"]))
            if self._node_jobs:
                work.append(("queue", self._node_jobs))
            if self._publication and not self._publication["ready"]:
                work.append(("single", self._publication["work"]))
            if self._maintenance:
                work.append(("queue", self._maintenance))
            if self._stop_requested:
                continue
            if work:
                kind, selected = work[turn % len(work)]
                if kind == "queue":
                    self._advance(selected)
                else:
                    next(selected, None)
                turn += 1
            else:
                delay = .01
                if self._pending:
                    delay = max(0, min(delay, self._due(self._pending[0]) - time.monotonic()))
                time.sleep(delay)
        self._record("stopped" if self._stop_requested else "drained", completed=time.monotonic())
        return self

    def close(self):
        errors = []
        if self._loader is not None:
            self._loader.shutdown(wait=True)
            self._loader = None
        if self._channel is not None:
            try:
                self._channel.close()
            except BaseException as error:
                errors.append(error)
            self._channel = None
        if self._journal is not None:
            try:
                self._journal.close()
            except BaseException as error:
                errors.append(error)
            self._journal = None
        if errors:
            raise ExceptionGroup("Runner close failed", errors)

    def __enter__(self):
        return self.prepare()

    def __exit__(self, kind, error, traceback):
        try:
            self.close()
        except BaseException as cleanup:
            if error is None:
                raise
            error.add_note(f"cleanup also failed: {cleanup}")
