#include "mul_scalar_op_rtovr_inst.h"

#include "mul_scalar_op_constants.h"

namespace aiacc {
namespace op {

void mul_scalar_gen_rtovr_to_fpmul(iq::RtoVrCru& cru) {
    cru.para_vld = true;
    for (int i = 0; i < 40; ++i) {
        cru.para_out[i].para_valid = false;
        cru.para_out[i].para_select = 0;
    }

    cru.para_out[RTOVR_OUT_DVR_FPMUL_S0].para_valid = true;
    cru.para_out[RTOVR_OUT_DVR_FPMUL_S0].para_select = RTOVR_IN_UR_RDATA_0;

    cru.para_out[RTOVR_OUT_DVR_FPMUL_S1].para_valid = true;
    cru.para_out[RTOVR_OUT_DVR_FPMUL_S1].para_select = RTOVR_IN_UR_RDATA_1;
}

void mul_scalar_gen_rtovr_fpmul_to_ur(iq::RtoVrCru& cru) {
    cru.para_vld = true;
    for (int i = 0; i < 40; ++i) {
        cru.para_out[i].para_valid = false;
        cru.para_out[i].para_select = 0;
    }

    cru.para_out[RTOVR_OUT_UVR_WDATA_0].para_valid = true;
    cru.para_out[RTOVR_OUT_UVR_WDATA_0].para_select = RTOVR_IN_DR_FPMUL_D;
}

} // namespace op
} // namespace aiacc
