#include "mul_scalar_op_ur_inst.h"

#include "mul_scalar_op.h"

namespace aiacc {
namespace op {

void gen_ur_read_inst(const MulScalarOpParams& params,
                      uint32_t step,
                      iq::UrCru& cru) {
    cru.para_vld = true;

    // port0: input, normal increment.
    cru.para_raddr[0].bank = params.para_input_bank;
    cru.para_raddr[0].addr_word = params.para_input_addr + step;
    cru.para_re[0] = true;

    // port1: scalar, fixed address (true scalar semantics).
    cru.para_raddr[1].bank = params.para_other_bank;
    cru.para_raddr[1].addr_word = params.para_other_addr;
    cru.para_re[1] = true;

    cru.para_re[2] = false;
    cru.para_re[3] = false;

    cru.para_we[0] = false;
    cru.para_we[1] = false;
    cru.para_we[2] = false;
    cru.para_we[3] = false;
}

void gen_ur_write_inst(const MulScalarOpParams& params,
                       uint32_t step,
                       iq::UrCru& cru) {
    cru.para_vld = true;

    cru.para_re[0] = false;
    cru.para_re[1] = false;
    cru.para_re[2] = false;
    cru.para_re[3] = false;

    cru.para_waddr[0].bank = params.para_output_bank;
    cru.para_waddr[0].addr_word = params.para_output_addr + step;
    cru.para_we[0] = true;
    cru.para_wbe[0] = 0xFFFF;

    cru.para_we[1] = false;
    cru.para_we[2] = false;
    cru.para_we[3] = false;
}

} // namespace op
} // namespace aiacc
