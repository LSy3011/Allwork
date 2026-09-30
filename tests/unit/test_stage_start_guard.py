"""ADR-057: unsupported stage offsets must not be silently ignored."""
import pytest
from driver.assembler import PhysicalAssembler, UnrollAdmission


@pytest.mark.parametrize("offset", [-1, 0.5, True])
def test_invalid_offset_rejected(offset):
    with pytest.raises(ValueError, match="stage_start"):
        PhysicalAssembler(None).assemble([], stage_start=offset)


@pytest.mark.parametrize("ii, admitted", [(2, False), (1, True)])
def test_offset_requires_unroll(ii, admitted):
    admission = UnrollAdmission("test", {"x": (0, 0, 256)}) if admitted else None
    with pytest.raises(ValueError, match="stage_start"):
        PhysicalAssembler(None).assemble([], II=ii, admission=admission, stage_start=60)
