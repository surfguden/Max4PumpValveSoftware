import argparse
import traceback
from pumpvalve.logging_support import configure, logger
from pumpvalve.storage import VERSION, data_dir


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', action='store_true')
    parser.add_argument('--smoke-test', metavar='REPORT_PATH')
    parser.add_argument('--sdk-import-test', metavar='REPORT_PATH')
    args = parser.parse_args()
    if args.version:
        return 0
    if args.sdk_import_test:
        from pumpvalve.runtime_verification import verify_sdk_import
        return verify_sdk_import(args.sdk_import_test)
    from pumpvalve.runtime import run
    return run(gui=True, smoke_report=args.smoke_test)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        configure()
        logger.exception('Application startup failed')
        (data_dir() / 'startup-error.txt').write_text(traceback.format_exc(), encoding='utf-8')
        raise
