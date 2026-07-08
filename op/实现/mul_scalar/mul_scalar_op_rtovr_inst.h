#ifndef MUL_SCALAR_OP_RTOVR_INST_H
#define MUL_SCALAR_OP_RTOVR_INST_H

#include "../../iq/modules/rtovr_iq.h"

namespace aiacc {
namespace op {

void mul_scalar_gen_rtovr_to_fpmul(iq::RtoVrCru& cru);
void mul_scalar_gen_rtovr_fpmul_to_ur(iq::RtoVrCru& cru);

} // namespace op
} // namespace aiacc

#endif // MUL_SCALAR_OP_RTOVR_INST_H
