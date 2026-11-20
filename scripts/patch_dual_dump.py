"""Apply a narrowly scoped, reproducible native output patch to a separate build.

No equation or integration changes. Mixed P-SV forward pressure dumps additionally
write velocity at that exact step, under a distinct filename. Original checkout
must be retained. Reject previously patched or structurally different sources.
"""
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]);changes={}
def edit(relative,edits):
    p=root/relative;before=p.read_text();after=before
    for old,new in edits:
        if after.count(old)!=1:raise ValueError(f'Unexpected source anchor: {relative}: {old}')
        after=after.replace(old,new)
    changes[relative]={'before':hashlib.sha256(before.encode()).hexdigest(),'after':hashlib.sha256(after.encode()).hexdigest()}
    p.write_text(after)
edit('src/specfem2D/write_movie_output.f90',[
 ('    NTSTEP_BETWEEN_OUTPUT_IMAGES','    NTSTEP_BETWEEN_OUTPUT_IMAGES,imagetype_wavefield_dumps'),
 ('    any_acoustic,any_elastic,','    any_acoustic,any_elastic,P_SV,'),
 ('    call write_wavefield_dumps()',"""    call write_wavefield_dumps('wavefield')
    ! TopoWave dual_dump_v1: same-step diagnostic output, forward mixed P-SV only.
    if (SIMULATION_TYPE == 1 .and. P_SV .and. coupled_acoustic_elastic .and. &
        imagetype_wavefield_dumps == 4) then
      imagetype_wavefield_dumps = 2
      call write_wavefield_dumps('solid_velocity')
      imagetype_wavefield_dumps = 4
    endif""")])
edit('src/specfem2D/write_wavefield_dumps.F90',[
 ('  subroutine write_wavefield_dumps()','  subroutine write_wavefield_dumps(output_prefix)'),
 ('  !local variables','  character(len=*), intent(in) :: output_prefix\n\n  !local variables'),
 ("trim(OUTPUT_FILES)//'wavefield',it,SIMULATION_TYPE,'.bin'","trim(OUTPUT_FILES)//trim(output_prefix),it,SIMULATION_TYPE,'.bin'"),
 ("trim(OUTPUT_FILES)//'wavefield',it,SIMULATION_TYPE,'.txt'","trim(OUTPUT_FILES)//trim(output_prefix),it,SIMULATION_TYPE,'.txt'")])
(root/'dual_dump_patch.json').write_text(json.dumps(dict(capability='acoustic_elastic_double_dual_v1',changes=changes),indent=2)+'\n')
