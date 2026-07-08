#ifndef NEG_OP_UR_INST_H
#define NEG_OP_UR_INST_H

#include <cstdint>

namespace aiacc {
namespace op {

// UR read/write CODE 编码（兼容现有 IQ 宽度）
uint32_t neg_encode_ur_read(uint8_t bank, uint32_t addr);
uint32_t neg_encode_ur_write(uint8_t bank, uint32_t addr, uint16_t wbe = 0xFFFF);

} // namespace op
} // namespace aiacc

#endif // NEG_OP_UR_INST_H
