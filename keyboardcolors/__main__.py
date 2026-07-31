import sys

from .app import KeyboardColorsApp


def main():
    app = KeyboardColorsApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
