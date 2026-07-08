#include "neg_op_compute_inst.h"
#include "../../iq/modules/fpadd_iq.h"

namespace aiacc {
namespace op {

uint8_t neg_get_fpadd_ctrl(uint8_t dtype) {
    iq::FpAddCru cru;
    cru.para_inst_valid = true;
    cru.para_mode_is_32b = (dtype == 0);
    cru.para_update_st = false;
    cru.para_is_sub = true;  // 0 - x
    return iq::FpAddIq::pack_cru_fpadd(cru);
}

} // namespace op
} // namespace aiacc

