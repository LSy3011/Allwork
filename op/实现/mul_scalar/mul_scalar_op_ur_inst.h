#ifndef MUL_SCALAR_OP_UR_INST_H
#define MUL_SCALAR_OP_UR_INST_H

#include "../../iq/modules/ur_iq.h"

namespace aiacc {
namespace op {

struct MulScalarOpParams;

void gen_ur_read_inst(const MulScalarOpParams& params,
                      uint32_t step,
                      iq::UrCru& cru);

void gen_ur_write_inst(const MulScalarOpParams& params,
                       uint32_t step,
                       iq::UrCru& cru);

} // namespace op
} // namespace aiacc

#endif // MUL_SCALAR_OP_UR_INST_H
