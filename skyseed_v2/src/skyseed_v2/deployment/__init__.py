"""Static preparation and independent generated-deployment lifecycle."""
from .deployer import Deployer, DeploymentError
__all__ = ["Deployer", "DeploymentError"]
