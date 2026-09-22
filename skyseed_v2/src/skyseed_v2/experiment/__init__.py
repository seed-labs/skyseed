"""Experiment arrangement and explicit computation/execution owners."""
__all__ = ["ExperimentController", "Producer", "run_generated_experiment", "PreparationContext"]

def __getattr__(name):
    if name == "PreparationContext":
        from .preparation import PreparationContext
        return PreparationContext
    if name == "ExperimentController":
        from .controller import ExperimentController
        return ExperimentController
    if name == "Producer":
        from .producer import Producer
        return Producer
    if name == "run_generated_experiment":
        from .execution import run_generated_experiment
        return run_generated_experiment
    raise AttributeError(name)
