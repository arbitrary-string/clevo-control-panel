import sys

from .app import ClevoControlPanelApp


def main():
    app = ClevoControlPanelApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
