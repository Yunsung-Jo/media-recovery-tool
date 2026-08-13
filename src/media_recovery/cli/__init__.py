from __future__ import annotations

import argparse

from media_recovery.cli import carve, enhance, reconstruct


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="media-recovery",
        description=(
            "손상된 디스크 이미지에서 JPEG·AVI를 카빙하고 baseline JPEG를 "
            "구조적으로 복구합니다."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    carve_parser = subparsers.add_parser(
        "carve", help="디스크 이미지에서 JPEG·AVI를 추출", description=carve.DESCRIPTION
    )
    carve.configure_parser(carve_parser)
    carve_parser.set_defaults(_handler=carve.run)

    reconstruct_parser = subparsers.add_parser(
        "reconstruct",
        help="카빙한 JPEG를 구조적으로 복구",
        description=reconstruct.DESCRIPTION,
    )
    reconstruct.configure_parser(reconstruct_parser)
    reconstruct_parser.set_defaults(_handler=reconstruct.run)

    enhance_parser = subparsers.add_parser(
        "enhance",
        help="복구본에 썸네일 참조 보정을 적용",
        description=enhance.DESCRIPTION,
    )
    enhance.configure_parser(enhance_parser)
    enhance_parser.set_defaults(_handler=enhance.run)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "_handler", None)
    if handler is None:
        parser.print_help()
        return
    handler(args)
