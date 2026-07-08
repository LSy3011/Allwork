#ifndef MUL_SCALAR_OP_COMPUTE_INST_H
#define MUL_SCALAR_OP_COMPUTE_INST_H

#include "../../iq/modules/fpmul_iq.h"

namespace aiacc {
namespace op {

struct MulScalarOpParams;

void gen_fpmul_inst(const MulScalarOpParams& params, iq::FpMulCru& cru);

} // namespace op
} // namespace aiacc

#endif // MUL_SCALAR_OP_COMPUTE_INST_H
