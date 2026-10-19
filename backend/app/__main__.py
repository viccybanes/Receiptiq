"""Run the local application or a packaged OCR smoke check."""

import argparse
import multiprocessing


def main():
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description="ReceiptIQ local server")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--ocr-smoke", help="Recognize an image and print recognized lines without starting HTTP")
    arguments = parser.parse_args()
    if arguments.ocr_smoke:
        from .db import data_directory
        from .ocr import PaddleEngine

        print(PaddleEngine(data_directory()).recognize(arguments.ocr_smoke))
        return
    import uvicorn

    from .main import create_app
    from .system import apply_pending_location

    print(f"ReceiptIQ: http://127.0.0.1:{arguments.port}", flush=True)
    uvicorn.run(create_app(apply_pending_location()), host="127.0.0.1", port=arguments.port, access_log=False)


if __name__ == "__main__":
    main()
