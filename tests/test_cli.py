import csv
from io import BytesIO

import pytest
from PIL import Image

from media_recovery import cli


@pytest.mark.parametrize("args", [["--help"], ["carve", "--help"], ["reconstruct", "--help"], ["enhance", "--help"]])
def test_cli_help(args, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(args)

    assert exc.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_integrated_cli_synthetic_pipeline(tmp_path):
    image_bytes = BytesIO()
    Image.new("RGB", (160, 128), (48, 96, 144)).save(
        image_bytes, "JPEG", quality=90, subsampling=1
    )
    disk_path = tmp_path / "fixture.bin"
    disk_path.write_bytes(b"\x00" * 4096 + image_bytes.getvalue() + b"\x00" * 256)

    carved = tmp_path / "carved"
    cli.main(["carve", str(disk_path), "-o", str(carved)])
    originals = list((carved / "jpeg").glob("*.jpg"))
    assert len(originals) == 1

    reconstructed = tmp_path / "reconstructed"
    cli.main([
        "reconstruct",
        str(carved / "jpeg"),
        "-o",
        str(reconstructed),
        "--time-budget",
        "0",
        "-j",
        "1",
    ])
    with (reconstructed / "report.csv").open(newline="", encoding="utf-8") as stream:
        reconstruct_rows = list(csv.DictReader(stream))
    assert len(reconstruct_rows) == 1
    assert reconstruct_rows[0]["action"] == "CLEAN"

    enhanced = tmp_path / "enhanced"
    cli.main([
        "enhance",
        str(carved / "jpeg"),
        str(reconstructed),
        "-o",
        str(enhanced),
        "-j",
        "1",
    ])
    with (enhanced / "report_thumbref.csv").open(
        newline="", encoding="utf-8"
    ) as stream:
        enhance_rows = list(csv.DictReader(stream))
    assert len(enhance_rows) == 1
    assert enhance_rows[0]["status"].startswith("skip_")
