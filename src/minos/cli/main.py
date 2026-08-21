from argparse import ArgumentParser
from importlib.metadata import version


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        prog="minos",
        description="Explainable settlement intelligence for Polish hospital care.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {version('minos-health')}",
    )
    return parser


def main() -> None:
    parser = build_parser()
    parser.parse_args()
    parser.print_help()
