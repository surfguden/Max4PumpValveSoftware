import argparse
import sys
import traceback
from pumpvalve.logging_support import configure, logger
from pumpvalve.application import Application
from pumpvalve.storage import VERSION, data_dir


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', action='store_true')
    parser.add_argument('--smoke-test', metavar='REPORT_PATH')
    args = parser.parse_args()
    if args.version:
        return 0
    configure()
    from PySide6.QtWidgets import QApplication
    from pumpvalve.ui import MainWindow
    qt = QApplication(sys.argv[:1])
    app = Application()
    window = MainWindow(app)
    window.show()
    if args.smoke_test:
        from pumpvalve.smoke import smoke
        from PySide6.QtCore import QTimer
        QTimer.singleShot(100, lambda: smoke(qt, app, window, args.smoke_test))
    code = qt.exec()
    app.shutdown()
    return code


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        configure()
        logger.exception('Application startup failed')
        (data_dir() / 'startup-error.txt').write_text(traceback.format_exc(), encoding='utf-8')
        raise
