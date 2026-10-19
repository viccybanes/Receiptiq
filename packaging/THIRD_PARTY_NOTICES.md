# ReceiptIQ third-party notices

ReceiptIQ runs locally. The installer includes the Python application, native libraries and web interface. Models and Ollama are downloaded only after choosing a setup profile. ReceiptIQ does not grant rights beyond each upstream license.

| Component | License / terms | Source |
| --- | --- | --- |
| Python 3.11 | Python Software Foundation License | https://docs.python.org/3/license.html |
| Tauri / Wry / Tao | MIT or Apache-2.0 | https://github.com/tauri-apps/tauri |
| React | MIT | https://github.com/facebook/react |
| FastAPI / Starlette / Uvicorn / Pydantic | MIT / BSD-3-Clause / BSD-3-Clause / MIT | https://github.com/fastapi/fastapi |
| PaddlePaddle / PaddleOCR / PaddleX / PP-OCR model weights | Apache-2.0 | https://github.com/PaddlePaddle/PaddleOCR |
| OpenCV | Apache-2.0 | https://github.com/opencv/opencv |
| NumPy / SciPy / OpenBLAS | BSD-3-Clause; included BLAS/LAPACK notices apply | https://numpy.org/doc/stable/license.html |
| Pillow | MIT-CMU | https://github.com/python-pillow/Pillow |
| pypdfium2 / PDFium | Apache-2.0 or BSD-3-Clause; PDFium third-party notices apply | https://github.com/pypdfium2-team/pypdfium2 |
| LangChain | MIT | https://github.com/langchain-ai/langchain |
| Chroma | Apache-2.0 | https://github.com/chroma-core/chroma |
| Ollama 0.40.6 standalone Windows runtime | MIT; bundled inference/backend notices also apply | https://github.com/ollama/ollama/tree/v0.40.6 |
| Qwen3-VL 2B / 4B Instruct models | Apache-2.0 | https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct |
| EmbeddingGemma 300M | Gemma Terms of Use, including prohibited-use policy; use implies acceptance | https://ai.google.dev/gemma/terms |
| PaddleX visualization support fonts | Upstream font licenses apply; cached from the official Paddle model host | https://github.com/PaddlePaddle/PaddleX |
| Microsoft Edge WebView2 | Microsoft WebView2 Runtime terms; installed by the Microsoft bootstrapper when absent | https://developer.microsoft.com/microsoft-edge/webview2/ |

Installed Python distributions retain their included metadata/license files in the backend `_internal` directory. Rust dependency versions are recorded in `src-tauri/Cargo.lock`; the Python lock is `backend/requirements.lock`; frontend versions are in `frontend/package-lock.json`. PyInstaller's bootloader exception permits distribution of bundled applications under their own license: https://pyinstaller.org/en/stable/license.html.

The pinned runtime archive URL, release API, size and SHA-256 digest are in `runtime-manifest.json`. The downloaded ZIP is verified before execution. Ollama's MIT license and dependency notices remain alongside its extracted runtime. ReceiptIQ uses a dedicated model directory and disables Ollama cloud features. Model tags resolve to upstream model content; setup records the selected profile, and Ollama retains model blob digests in its manifest cache.

This development installer is unsigned unless release signing credentials are configured externally. No updater is enabled; offline operation does not check for application updates. A production release must audit the complete bundled dependency notices and sign the final installer with the publisher's certificate.
