#ifndef NEG_OP_RTOVR_INST_H
#define NEG_OP_RTOVR_INST_H

#include <cstdint>

namespace aiacc {
namespace op {

// RtoVR CODE 编码：source_id -> 7bit code
uint32_t neg_encode_rtovr_source(uint8_t source_id);

} // namespace op
} // namespace aiacc

#endif // NEG_OP_RTOVR_INST_H
