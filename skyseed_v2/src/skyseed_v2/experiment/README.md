# Experiment orchestration

`Producer` is the sole chronological calculation cursor. It holds the original
Timeline, physical Models and Planner plus the necessary compiler predecessor.
`ExperimentController` integrates it with input capture, optional presentation,
material publication and separately owned deployment/runtime operations.

```mermaid
classDiagram
    class ExperimentController {
        generate() Path
        serve(delivery_duration, run, on_completed) Path
        continue_calculation(additional_duration, end_time, delivery_duration)
        cancel_computation()
        cancel_runtime()
        status()
    }
    class Producer {
        _timeline : SimulationTimeline
        _previous_result : BpfCompilationResult
        produce(through, delivery_ends, completed)
        request_extension()
        produce_next_observation()
        produce_next_plan()
        produce_next_realization()
        finish()
        close()
    }
    ExperimentController *-- Producer
    ExperimentController --> Deployer : calls
    Producer o-- PhysicalWorld : advances
    Producer o-- Planner : advances
    Producer --> _GenerationHistory : publishes
```

`generate()` drains a finite request. `serve()` waits efficiently after each completed
range; continuation extends only its suffix. The Controller's optional presentation
worker coalesces completed requests independently of Producer/Runner progress.
[Output records](output/README.md) provide the detached read boundary.

`execution.py` owns invocation claims, Runner/Traffic startup, reset and shutdown.
`run_generated_experiment()` remains explicit finite deployment/execution automation.
Generated `experiment_control.py` is a client to live owners, not restart recovery.
[Experiment reference](../../../docs/development/experiment-control.md) defines command ownership;
[controls guide](../../../docs/user-guides/computation-and-controls.md) gives operations.
