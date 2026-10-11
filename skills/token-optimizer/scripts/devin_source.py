"""Source-only imports for the installed payload. Never trust cached bytecode."""
import importlib.abc
import importlib.util
from pathlib import Path
import sys

class SourceOnly(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, root):
        self.root=Path(root)
    def find_spec(self, fullname, path=None, target=None):
        if '.' in fullname:return None
        candidate=self.root/(fullname+'.py')
        if candidate.is_file():
            return importlib.util.spec_from_file_location(fullname,candidate,loader=self)
        return None
    def create_module(self,spec):return None
    def exec_module(self,module):
        source=Path(module.__spec__.origin)
        exec(compile(source.read_bytes(),str(source),'exec'),module.__dict__)

def activate(root):
    sys.meta_path.insert(0,SourceOnly(root))
