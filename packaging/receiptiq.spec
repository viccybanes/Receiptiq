# Build from the project root: .venv\Scripts\pyinstaller packaging\receiptiq.spec
from pathlib import Path
from importlib.metadata import requires
from packaging.requirements import Requirement
from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata

root = Path(SPECPATH).parent
datas = [(str(root / "frontend" / "dist"), "frontend/dist")]
datas += [(str(root / "packaging" / name), "packaging")
          for name in ("runtime-manifest.json", "THIRD_PARTY_NOTICES.md")]
binaries = []
hidden = ["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.h11_impl",
          "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
          "langchain_community.vectorstores.faiss", "langchain_community.embeddings.openai",
          "scipy._cyutility"]
for package in ("paddle", "paddlex", "paddleocr", "pyclipper", "shapely", "chardet"):
    package_data, package_binaries, package_hidden = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hidden += package_hidden
for package in ("paddleocr", "paddlex", "paddlepaddle", "pypdfium2", "langchain", "langchain_core",
                "langchain_community", "langchain_openai"):
    datas += copy_metadata(package)
# PaddleX checks installed distribution metadata for every OCR extra at runtime.
for dependency in requires("paddlex"):
    requirement = Requirement(dependency)
    if requirement.marker is None or requirement.marker.evaluate({"extra": "ocr"}):
        datas += copy_metadata(requirement.name)
datas += collect_data_files("pypdfium2_raw")
datas += collect_data_files("langchain_community", include_py_files=True)
# Setuptools adds its vendored packages to sys.path at runtime; static analysis cannot see those aliases.
datas += collect_data_files("setuptools", include_py_files=True)
analysis = Analysis([str(root / "packaging" / "entry.py")], pathex=[str(root / "backend")],
                    binaries=binaries, datas=datas, hiddenimports=hidden,
                    excludes=["tkinter", "matplotlib", "torch", "tensorflow", "pytest", "IPython"])
archive = PYZ(analysis.pure)
executable = EXE(archive, analysis.scripts, [], exclude_binaries=True, name="receiptiq",
                 console=True, upx=False)
collection = COLLECT(executable, analysis.binaries, analysis.datas, strip=False, upx=False, name="receiptiq")
