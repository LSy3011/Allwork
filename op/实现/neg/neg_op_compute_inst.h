#ifndef NEG_OP_COMPUTE_INST_H
#define NEG_OP_COMPUTE_INST_H

#include <cstdint>

namespace aiacc {
namespace op {

// FPADD 控制字（3bit），根据 dtype 选择 fp32/fp16 的减法模式。
uint8_t neg_get_fpadd_ctrl(uint8_t dtype);

} // namespace op
} // namespace aiacc

#endif // NEG_OP_COMPUTE_INST_H
