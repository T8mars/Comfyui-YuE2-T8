"""Initialize packaged dynamic llama.cpp backends before capability checks."""
import ctypes
import json
import os
import sys
from pathlib import Path

_DLL_HANDLES = []


def initialize_backends():
    cuda = Path(sys.executable).resolve().parent / "cuda"
    if os.name == "nt" and cuda.is_dir() and not _DLL_HANDLES:
        _DLL_HANDLES.append(os.add_dll_directory(str(cuda)))
        os.environ["PATH"] = str(cuda) + os.pathsep + os.environ.get("PATH", "")
    import llama_cpp
    from llama_cpp._ggml import ggml_backend_load_all_from_path
    llama_cpp.llama_backend_init()
    directory = Path(llama_cpp.__file__).resolve().parent / "lib"
    ggml_backend_load_all_from_path(ctypes.c_char_p(str(directory).encode("utf-8")))
    return {"python": sys.version, "llama_cpp": llama_cpp.__version__,
            "gpu_offload": bool(llama_cpp.llama_supports_gpu_offload()), "module": llama_cpp.__file__}


if __name__ == "__main__":
    print(json.dumps(initialize_backends()))
