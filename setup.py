"""Build the active package without reusing removed modules from prior builds."""
from pathlib import Path
import shutil
from setuptools import setup
from setuptools.command.build_py import build_py


class FreshPackageBuild(build_py):
    def run(self):
        destination = Path(self.build_lib).resolve() / "skyseed"
        source = Path(self.get_package_dir("skyseed")).resolve()
        build_root = Path(self.get_finalized_command("build").build_base).resolve()
        if (not destination.is_relative_to(build_root)
                or source.is_relative_to(destination) or destination.is_relative_to(source)):
            raise RuntimeError("package build directory must be separate from source and inside build_base")
        if destination.exists():
            shutil.rmtree(destination)
        super().run()


setup(cmdclass={"build_py": FreshPackageBuild})
