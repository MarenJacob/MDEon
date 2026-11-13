"""`python -m eon <jarvis args>` runs the OpenJarvis CLI with EON installed."""

from __future__ import annotations


def main() -> None:
    from eon.bootstrap import install

    install()
    from openjarvis.cli import main as jarvis_main

    jarvis_main()


if __name__ == "__main__":
    main()
