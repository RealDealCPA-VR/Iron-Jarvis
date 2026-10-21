"""Reference solution: run the generator, as the task asks."""
import runpy
import sys

sys.argv = ["tools/make_manifest.py"]
runpy.run_path("tools/make_manifest.py", run_name="__main__")
