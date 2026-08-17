import uvicorn
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

cuda_bin = Path(
    r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin"
)

cudnn_bin = (
    BASE_DIR
    / ".venv"
    / "Lib"
    / "site-packages"
    / "nvidia"
    / "cudnn"
    / "bin"
)

extra_paths = []

if cuda_bin.exists():
    extra_paths.append(str(cuda_bin))

if cudnn_bin.exists():
    extra_paths.append(str(cudnn_bin))

if extra_paths:
    os.environ["PATH"] = (
        os.pathsep.join(extra_paths)
        + os.pathsep
        + os.environ.get("PATH", "")
    )

print("CUDA path:", cuda_bin)
print("cuDNN path:", cudnn_bin)
if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
