from media_recovery.domain.objects import FileHit


def test_filehit_jpeg():
    hit = FileHit(file_type='jpeg', offset=0x1000)
    assert hit.file_type == 'jpeg'
    assert hit.offset == 0x1000


def test_filehit_avi():
    hit = FileHit(file_type='avi', offset=0x2000)
    assert hit.file_type == 'avi'
    assert hit.offset == 0x2000


def test_filehit_equality():
    assert FileHit('jpeg', 100) == FileHit('jpeg', 100)
    assert FileHit('jpeg', 100) != FileHit('jpeg', 200)
