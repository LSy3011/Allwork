#ifndef MUL_SCALAR_OP_CONSTANTS_H
#define MUL_SCALAR_OP_CONSTANTS_H

#include <cstdint>

namespace aiacc {
namespace op {

constexpr uint8_t RTOVR_IN_DR_FPMUL_D = 3;
constexpr uint8_t RTOVR_IN_UR_RDATA_0 = 30;
constexpr uint8_t RTOVR_IN_UR_RDATA_1 = 31;

constexpr uint8_t RTOVR_OUT_DVR_FPMUL_S0 = 3;
constexpr uint8_t RTOVR_OUT_DVR_FPMUL_S1 = 4;
constexpr uint8_t RTOVR_OUT_UVR_WDATA_0 = 35;

} // namespace op
} // namespace aiacc

#endif // MUL_SCALAR_OP_CONSTANTS_H
