# Build from the project root: .venv\Scripts\pyinstaller packaging\receiptiq.spec
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata

root = Path(SPECPATH).parent
datas = [(str(root / "frontend" / "dist"), "frontend/dist")]
binaries = []
hidden = ["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.h11_impl",
          "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
          "langchain_community.vectorstores.faiss", "langchain_community.embeddings.openai"]
for package in ("paddle", "paddlex", "paddleocr", "pyclipper", "shapely"):
    package_data, package_binaries, package_hidden = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hidden += package_hidden
for package in ("paddleocr", "paddlex", "paddlepaddle", "pypdfium2", "langchain", "langchain_core",
                "langchain_community", "langchain_openai"):
    datas += copy_metadata(package)
datas += collect_data_files("pypdfium2_raw")
analysis = Analysis([str(root / "packaging" / "entry.py")], pathex=[str(root / "backend")],
                    binaries=binaries, datas=datas, hiddenimports=hidden,
                    excludes=["tkinter", "matplotlib", "torch", "tensorflow", "pytest", "IPython"])
archive = PYZ(analysis.pure)
executable = EXE(archive, analysis.scripts, [], exclude_binaries=True, name="receiptiq",
                 console=True, upx=False)
collection = COLLECT(executable, analysis.binaries, analysis.datas, strip=False, upx=False, name="receiptiq")
