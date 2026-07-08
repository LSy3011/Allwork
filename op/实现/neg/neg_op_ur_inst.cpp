#include "neg_op_ur_inst.h"

namespace aiacc {
namespace op {

uint32_t neg_encode_ur_read(uint8_t bank, uint32_t addr) {
    return 1u | (static_cast<uint32_t>(bank & 0x7) << 1) | ((addr & 0xFFu) << 4);
}

uint32_t neg_encode_ur_write(uint8_t bank, uint32_t addr, uint16_t wbe) {
    return 1u |
           (static_cast<uint32_t>(bank & 0x7) << 1) |
           ((addr & 0xFFu) << 4) |
           (static_cast<uint32_t>(wbe & 0xFFFF) << 12);
}

} // namespace op
} // namespace aiacc

