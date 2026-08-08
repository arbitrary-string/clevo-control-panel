import os
import sys

from .app import ClevoControlPanelApp


def main():
    argv = sys.argv
    minimized = "--minimized" in argv
    if minimized:
        argv = [a for a in argv if a != "--minimized"]

    main_exec = os.path.abspath(argv[0])
    app = ClevoControlPanelApp(minimized=minimized, main_exec=main_exec)
    return app.run(argv)


if __name__ == "__main__":
    sys.exit(main())
