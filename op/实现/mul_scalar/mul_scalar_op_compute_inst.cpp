#include "mul_scalar_op_compute_inst.h"
#include "mul_scalar_op.h"

namespace aiacc {
namespace op {

void gen_fpmul_inst(const MulScalarOpParams& params, iq::FpMulCru& cru) {
    cru.para_inst_valid = true;
    const bool is_32b = (params.para_dtype == MulScalarDataType::FP32);
    cru.para_src_precision_is_32b = is_32b;
    cru.para_dst_precision_is_32b = is_32b;
}

} // namespace op
} // namespace aiacc
