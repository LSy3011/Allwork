#ifndef MUL_SCALAR_OP_CONSTANTS_H
#define MUL_SCALAR_OP_CONSTANTS_H

#include <cstdint>

namespace aiacc {
namespace op {

constexpr uint8_t RTOVR_IN_DR_FPMUL_D = 3;
constexpr uint8_t RTOVR_IN_DR_VIN0_D = 11;
constexpr uint8_t RTOVR_IN_DR_VIN1_D = 12;
constexpr uint8_t RTOVR_IN_DR_VOUT0_D = 13;
constexpr uint8_t RTOVR_IN_UR_RDATA_0 = 30;
constexpr uint8_t RTOVR_IN_UR_RDATA_1 = 31;

constexpr uint8_t RTOVR_OUT_DVR_FPMUL_S0 = 3;
constexpr uint8_t RTOVR_OUT_DVR_FPMUL_S1 = 4;
constexpr uint8_t RTOVR_OUT_DVR_VIN0_S = 14;
constexpr uint8_t RTOVR_OUT_DVR_VIN1_S = 15;
constexpr uint8_t RTOVR_OUT_DVR_VOUT0_S = 16;
constexpr uint8_t RTOVR_OUT_UVR_WDATA_0 = 35;

// FPGA v4.22 maps these formerly reserved IQ slots to VIN/VOUT controls.
constexpr uint32_t MUL_SCALAR_IQ_ID_VIN0 = 68;
constexpr uint32_t MUL_SCALAR_IQ_ID_VIN1 = 69;
constexpr uint32_t MUL_SCALAR_IQ_ID_VOUT0 = 70;
constexpr uint16_t MUL_SCALAR_VIN_VOUT_CODE_BITS = 7;

} // namespace op
} // namespace aiacc

#endif // MUL_SCALAR_OP_CONSTANTS_H
